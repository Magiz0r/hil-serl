"""AutoSERL intervention logic adapted for the existing HIL-SERL stack.

Source: https://github.com/autoserl/AutoSERL
Commit: 978f11a9a25cbb6c13ad691df4e6f3156568c378
Original class: franka_env/envs/wrappers.py::auto_intervention_wrapper
Changes and verification limits: reproduction/autoserl/README.md.

The injected expert supplies success/abort buttons. This module opens no devices.
"""
import copy
import pickle

import gymnasium as gym
import numpy as np
from scipy.spatial.transform import Rotation as R

class AutoIntervention(gym.ActionWrapper):
    def __init__(self, env, target_pose, *, demo_path, demo_initial_tcp_pose,
                 expert, th1=0.005, th2=0.02, l_term=10, l_stag=20,
                 recover_point0=35, recover_point1=None,
                 demo_pose_format="euler", enable_interventions=True):
        super().__init__(env)
        self.demo_path = demo_path
        self.demo_tcp_list = []
        self.target_pose = target_pose
        # params
        ####################################
        self.expert = expert
        self.enable_interventions = enable_interventions
        self.left, self.right = False, False
        self.intervened_slide_idx = -1
        self.demo_initial_tcp_pose = np.asarray(demo_initial_tcp_pose, dtype=float)
        self.demo_pose_format = demo_pose_format
        self.demo_action_in_eeframe_list = []
        self.load_intervention_demo()
        self.demo_tcp_buffer = []
        self.demo_tcp_buffer.append(copy.deepcopy(self.demo_tcp_list))
        self.current_demo_tcp_list = copy.deepcopy(self.demo_tcp_list)
        self.total_recover_cnt = 0
        self.total_episode_cnt = 0

        self.trans_dist_threshold = th2
        self.intervention_conclusion_trans_threshold = th1
        self.continued_control_step_cnt_threshold = l_stag
        self.intervention_termination_step_threshold = l_term
        self.intervened_slide_idx_buffer_recover_threshold = 1

        self.recover_index0 = recover_point0
        if recover_point1 != None:
            self.recover_index1 = recover_point1
        else:
            self.recover_index1 = len(self.current_demo_tcp_list)-1
        if not (0 <= self.recover_index0 < self.recover_index1 < len(self.current_demo_tcp_list)):
            raise ValueError("Recovery points must satisfy 0 <= point0 < point1 < demo length")
        if not (np.isfinite(th1) and np.isfinite(th2) and 0 < th1 < th2) or l_stag < 2 or l_term < 0:
            raise ValueError("Require 0 < th1 < th2, l_stag >= 2, l_term >= 0")
        self.window_length = (self.recover_index1 - self.recover_index0 + 1)

        self.slide_window = [i for i in range(self.window_length)]
        self.recover_action_list = []
        self.intervened_slide_idx_buffer = {i: [] for i in range(len(self.current_demo_tcp_list))}
        self.intervened_slide_idx_buffer[-1] = []
        self.current_step = 0
        self.before_intervened_completed_traj_min_idx = -1
        self.detect_nomove_window = [np.array([100, 100, 100, 0, 0, 0, 1]) for i in range(self.continued_control_step_cnt_threshold)]

        self.intervention_cnt = 0
        self.total_intervention_cnt = 0
        self.forever_no_window_intervention = False
        self.stagnation_pose = np.array([100, 100, 100, 0, 0, 0, 1])

    def load_intervention_demo(self):
        # Load only trusted, locally produced pickle files.
        if self.demo_pose_format not in ("euler", "quaternion"):
            raise ValueError("demo_pose_format must explicitly be euler or quaternion")
        if self.demo_initial_tcp_pose.shape != (6,) or not np.isfinite(self.demo_initial_tcp_pose).all():
            raise ValueError("demo_initial_tcp_pose must be finite xyz + Euler xyz (6,)")
        with open(self.demo_path, 'rb') as f:
            demo_data = pickle.load(f)
            if not isinstance(demo_data, list) or len(demo_data) < 2:
                raise ValueError("A demonstration needs at least two transitions")
            if sum(bool(d.get("dones", False)) for d in demo_data) != 1 or not demo_data[-1].get("dones", False) or demo_data[-1].get("rewards", 0) <= 0:
                raise ValueError("Expected exactly one complete successful demonstration")
            demo_tcp_list = []
            demo_data_list = demo_data
            for data in demo_data_list:
                state = np.asarray(data['observations']['state'])
                expected = 19 if self.demo_pose_format == "euler" else 20
                if state.shape != (1, expected) or not np.isfinite(state).all():
                    raise ValueError(f"Expected finite state (1, {expected}) for {self.demo_pose_format}; got {state.shape}")
                # SERLObsWrapper sorts keys: gripper(1), force(3), pose,
                # torque(3), velocity(6). Current Quat2Euler emits a 6D pose.
                if self.demo_pose_format == "euler":
                    pose = np.r_[state[0, 4:7], R.from_euler("xyz", state[0, 7:10]).as_quat()]
                else:
                    pose = state[0, 4:11].copy()
                    pose[3:] = R.from_quat(pose[3:]).as_quat()
                action = np.asarray(data['actions'])
                if action.shape != (6,) or not np.isfinite(action).all():
                    raise ValueError("AutoSERL requires finite 6D fixed-gripper actions")
                demo_tcp_list.append(pose)
            curr_demo_tcp_list = np.array(copy.deepcopy(demo_tcp_list))

            curr_demo_tcp_list[:, :3] = R.from_euler("xyz", np.tile(self.demo_initial_tcp_pose[3:], (len(curr_demo_tcp_list), 1))).apply(curr_demo_tcp_list[:, :3]) + np.tile(self.demo_initial_tcp_pose[:3], (len(curr_demo_tcp_list), 1))
            curr_demo_tcp_list[:, 3:] = (R.from_euler("xyz", np.tile(self.demo_initial_tcp_pose[3:], (len(curr_demo_tcp_list), 1)))*R.from_quat(curr_demo_tcp_list[:, 3:])).as_quat()

            self.demo_tcp_list = curr_demo_tcp_list
            self.demo_action_in_eeframe_list = [np.asarray(data["actions"]).copy() for data in demo_data]

    def select_minidist_point(self, tcp_pose, input_curr_demo_tcp_list):

        curr_demo_tcp_list = copy.deepcopy(input_curr_demo_tcp_list)
        tcp_pose = np.array(tcp_pose)

        demo_tcp_pos = curr_demo_tcp_list[:, :3]
        demo_tcp_quat = curr_demo_tcp_list[:, 3:]

        trans_dist = np.linalg.norm(demo_tcp_pos - tcp_pose[:3], axis=1)

        tcp_rot = R.from_quat(tcp_pose[3:])
        demo_rot = R.from_quat(demo_tcp_quat)
        relative_rot = demo_rot.inv() * tcp_rot
        rot_dist = relative_rot.magnitude()

        min_idx = np.argmin(trans_dist)

        return trans_dist[min_idx], rot_dist[min_idx], min_idx

    def compute_delta_pose(self, curr_pose, target_pose):
        t_curr, q_curr = np.array(curr_pose[:3]), np.array(curr_pose[3:])
        t_tgt,  q_tgt  = np.array(target_pose[:3]), np.array(target_pose[3:])

        r_curr = R.from_quat(q_curr)
        r_tgt  = R.from_quat(q_tgt)

        r_rel = (r_tgt*r_curr.inv()).as_rotvec()/self.action_scale[1]
        t_rel = (t_tgt - t_curr)/self.action_scale[0]

        delta_pose = np.clip(np.concatenate([t_rel, r_rel]), -1, 1)
        return delta_pose

    def find_closest_intervention_point(self, min_idx):
        point0 = self.recover_index0
        point1 = self.recover_index1

        if (min_idx in range(point0, point1+1)) and (len(self.intervened_slide_idx_buffer[self.recover_index0]) >= self.intervened_slide_idx_buffer_recover_threshold):
            return self.recover_index0
        return -1

    def judge_whether_true_intervention(self, intervention_point, completed_traj_min_idx):
        if completed_traj_min_idx == len(self.current_demo_tcp_list) - 1:
            demo_point0 = self.current_demo_tcp_list[completed_traj_min_idx-1]
            demo_point1 = self.current_demo_tcp_list[completed_traj_min_idx]
        else:
            demo_point0 = self.current_demo_tcp_list[completed_traj_min_idx]
            demo_point1 = self.current_demo_tcp_list[completed_traj_min_idx+1]
        direction = demo_point1[:3] - demo_point0[:3]
        current_direction = intervention_point[:3] - self.env.currpos[:3]
        direction_norm = np.linalg.norm(direction)
        current_norm = np.linalg.norm(current_direction)
        if direction_norm < 1e-12 or current_norm < 1e-12:
            return False
        direction = direction / direction_norm
        current_direction = current_direction / current_norm
        cos_theta = np.dot(direction, current_direction)
        if cos_theta >= 0:
            return True
        else:
            return False

    def check_action_converged(self, pos_threshold=0.001, rot_threshold=0.01):
        pos_error = np.linalg.norm(self.env.currpos[:3] - self.prev_currpos[:3])

        r_curr = R.from_quat(self.env.currpos[3:])
        r_prev = R.from_quat(self.prev_currpos[3:])
        rot_error = (r_prev.inv() * r_curr).magnitude()
        converged = pos_error < pos_threshold and rot_error < rot_threshold

        return converged

    def cal_action(self, action: np.ndarray) -> np.ndarray:
        if not self.enable_interventions:
            return np.asarray(action).copy(), False
        intervened = False
        trans_dist, rot_dist, min_idx = self.select_minidist_point(copy.deepcopy(self.env.currpos), copy.deepcopy(self.current_demo_tcp_list[np.array(self.slide_window)]))
        completed_traj_trans_dist, completed_traj_rot_dist, completed_traj_min_idx = self.select_minidist_point(copy.deepcopy(self.env.currpos), copy.deepcopy(self.current_demo_tcp_list))

       # Sliding Window Intervention
        if (trans_dist > self.trans_dist_threshold) and (self.before_intervened_completed_traj_min_idx == -1) and (not self.forever_no_window_intervention):
            if self.judge_whether_true_intervention(self.current_demo_tcp_list[self.slide_window[min_idx]], completed_traj_min_idx):
                self.intervened_slide_idx = self.slide_window[min_idx]

        # Safety Recovery Mechanism
        idx = self.find_closest_intervention_point(completed_traj_min_idx)
        if len(self.detect_nomove_window) == self.continued_control_step_cnt_threshold:
            self.detect_nomove_window.pop(0)
        self.detect_nomove_window.append(copy.deepcopy(self.current_demo_tcp_list[completed_traj_min_idx]))
        if ((self.before_intervened_completed_traj_min_idx == -1) and (idx != -1)) and (not self.forever_no_window_intervention):
            self.intervened_slide_idx = copy.deepcopy(idx)
            recovery_end = self.intervened_slide_idx_buffer[self.intervened_slide_idx][-1]
            self.recover_action_list = [a.copy() for a in self.demo_action_in_eeframe_list[self.intervened_slide_idx:recovery_end + 1]]
            self.before_intervened_completed_traj_min_idx = copy.deepcopy(idx)
            self.total_recover_cnt += 1

        if (self.intervened_slide_idx != -1):
            target_point = copy.deepcopy(self.current_demo_tcp_list[self.intervened_slide_idx])
            tcp_rot = R.from_quat(self.env.currpos[3:])
            demo_rot = R.from_quat(target_point[3:])
            relative_rot = demo_rot.inv() * tcp_rot
            rot_dist = relative_rot.magnitude()  # gives the rotation angle (radians)
            trans_dist = np.linalg.norm(target_point[:3] - self.env.currpos[:3])
            if (trans_dist < self.intervention_conclusion_trans_threshold) or (self.check_action_converged() and (np.linalg.norm(self.env.currpos[:3] - self.stagnation_pose[:3]) > self.intervention_conclusion_trans_threshold)):
                if len(self.intervened_slide_idx_buffer[self.before_intervened_completed_traj_min_idx]) >= self.intervened_slide_idx_buffer_recover_threshold:
                    self.intervened_slide_idx_buffer[self.before_intervened_completed_traj_min_idx] = []
                self.intervened_slide_idx = -1

        if ((self.before_intervened_completed_traj_min_idx == -1) and (np.linalg.norm(self.detect_nomove_window[-1][:3] - self.detect_nomove_window[0][:3]) < self.intervention_conclusion_trans_threshold) and (completed_traj_min_idx >= self.recover_index0) and (completed_traj_min_idx <= self.recover_index1)) and (not self.forever_no_window_intervention):
            self.intervened_slide_idx = -1
            self.intervened_slide_idx_buffer[self.recover_index0].append(self.recover_index1)
            self.stagnation_pose = copy.deepcopy(self.env.currpos)

        if (self.intervened_slide_idx != -1):
            expert_a = self.compute_delta_pose(copy.deepcopy(self.env.currpos), copy.deepcopy(self.current_demo_tcp_list[self.intervened_slide_idx]))[:6]
            intervened = True

        if (len(self.recover_action_list) > 0) and (self.intervened_slide_idx_buffer[self.before_intervened_completed_traj_min_idx] == []):
            expert_a = self.transform_action(self.recover_action_list.pop(0))
            intervened = True
            if len(self.recover_action_list) == 0:
                self.before_intervened_completed_traj_min_idx = -1
                self.stagnation_pose = np.array([100, 100, 100, 0, 0, 0, 1])
        if intervened:
            return expert_a, True
        return action, False

    def step(self, action):
        _, buttons = self.expert.get_action()
        self.left, self.right = tuple(buttons)

        new_action, replaced = self.cal_action(action)
        if replaced:
            self.intervention_cnt += 1
            new_action = self.transform_action_inv(new_action)
        self.prev_currpos = copy.deepcopy(self.env.currpos)
        obs, rew, done, truncated, info = self.env.step(new_action)
        rew = (self.left)

        done = bool(done or rew or self.right)
        info['succeed'] = bool(rew)
        info['auto_intervention'] = bool(replaced)
        info['auto_recovery_count'] = self.total_recover_cnt

        # Intervention Termination
        if (self.intervention_cnt < self.intervention_termination_step_threshold) and (rew == 1) and done:
            self.forever_no_window_intervention = True

        self.current_step += 1

        if replaced:
            info["intervene_action"] = new_action
        if (self.intervened_slide_idx == -1):
            self.slide_window.pop(0)
            self.slide_window.append(min(self.slide_window[-1]+1, len(self.current_demo_tcp_list)-1))

        return obs, rew, done, truncated, info

    def reset(self, **kwargs):
        self.total_episode_cnt += 1
        self.intervened_slide_idx = -1
        obs, info = self.env.reset(**kwargs)
        self.prev_currpos = copy.deepcopy(self.env.currpos)
        self.slide_window = [i for i in range(self.window_length)]

        self.recover_action_list = []
        self.current_step = 0
        self.detect_nomove_window = [np.array([100, 100, 100, 0, 0, 0, 1]) for i in range(self.continued_control_step_cnt_threshold)]
        self.stagnation_pose = np.array([100, 100, 100, 0, 0, 0, 1])
        self.before_intervened_completed_traj_min_idx = -1
        self.intervention_cnt = 0
        self.total_intervention_cnt = 0
        self.intervened_slide_idx_buffer = {i: [] for i in range(len(self.current_demo_tcp_list))}
        self.intervened_slide_idx_buffer[-1] = []
        return obs, info

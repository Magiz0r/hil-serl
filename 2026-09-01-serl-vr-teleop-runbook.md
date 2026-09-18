# SERL VR teleoperation bring-up

Goal: Quest/WebXR controller pose -> `VRPolicy` clutch -> absolute Cartesian
target -> SERL Cartesian impedance -> FR3. This is U1 of the full upgrade; XRT
pose and stereo video remain later phases.

## Recorded NUC build (2026-09-01)

- NUC build context: `/home/tasl/remote_teleop_serl_build`
- Base image: `rlinf-libfranka0181:2026-05-21`
- Base SERL checkout commit: `1f140ef0d8e3fc443569c193d3ede1856e50d521`
- Derived image: `remote-teleop-serl:2026-09-01`
- Derived image ID/digest:
  `sha256:e0f49ed76e33fac7614dfd3e86f0f3bf9344ea73709713a45b7b52dc91256949`
- Image size reported by Docker: `5073244077` bytes
- Build result: initialization patch applied; `serl_franka_controllers`
  compiled and linked successfully with `catkin_make --pkg ... -j1`.
- Offline checks: patched lines present, controller shared library non-empty,
  and bridge CLI imports successfully with networking disabled.
- Idle deployment container: `remote-teleop-serl`, created from the derived
  image with ID `a19a257b4a1a57772c26b07fe0e65018c760f3c81c64dc2395d78d610b4b20a7`.
  Verified configuration: `cmd=["sleep","infinity"]`, host network,
  privileged, no mounts. Its only process was `sleep infinity`; no ROS process
  or control port was active.

The build context is a deployment copy of four files from this repository,
not another source-of-truth checkout. The existing `/home/tasl/RLinf`, original
SERL checkout, `rlinf-explore` container, and base image were not edited. All
authored source, patch, Docker recipe, tests, and operational notes live in
this `remote_teleop` repository.

The deployment container intentionally has no host source mounts. In
particular, it does not mount `/home/tasl/RLinf`. The scripts under
`deploy/serl` first perform read-only exclusivity checks and then create a
container whose default command is only `sleep infinity`; ROS startup is kept
as a separate hardware boundary.

### Soft first-contact profile

The operational image additionally applies
[`serl_franka_controllers-soft-bringup-defaults.patch`](../patches/serl_franka_controllers-soft-bringup-defaults.patch):

| Parameter | Original | First-contact |
|---|---:|---:|
| Translational stiffness | 2000 N/m | 300 N/m |
| Translational damping | 89 | 35 |
| Rotational stiffness | 150 Nm/rad | 20 Nm/rad |
| Rotational damping | 7 | 9 |
| Joint-1 nullspace stiffness | 100 | 10 |
| Translation error clipping | 10 mm | 5 mm |
| Rotation error clipping | 0.05 rad | 0.03 rad |

The damping values are approximately critically damped under the controller's
simple `2*sqrt(stiffness)` convention. This profile is for initial takeover and
millimetre-scale checks; final values must be selected from observed tracking,
sag, oscillation, and operator feel.

Recorded soft build on NUC1:

- Image: `remote-teleop-serl:2026-09-01-soft`
- Image ID/digest:
  `sha256:c5223082bce80ac1f299eb96c2b0ec486d8e831c63e7a5f7b60d857c5ef5a1e2`
- Idle container: `remote-teleop-serl-soft`, ID
  `ab828f460dd5c3042c65ae20b9c8523aac66e89b33c37721a33690df80e1795b`
- Generated dynamic-reconfigure defaults were imported from the built image
  and verified as `300, 35, 20, 9, 10, 0.005, 0.03` for the parameters in the
  table above.
- Container verification: only `sleep infinity`, no mounts, no ROS/controller
  process launched.

## Current no-motion validation

```bash
source .venv/bin/activate
pytest -q
python -m py_compile rteleop/robot/serl_client.py \
  rteleop/tools/serl_ros_bridge.py rteleop/apps/serl_teleop_fr3.py
```

The tests exercise clutch invariants, target workspace/distance/state-age
rejection, libfranka transform conversion, and a complete client/bridge TCP
round trip without ROS or robot hardware.

For a running hold controller, `rteleop/tools/serl_hold_probe.py` is a
read-only ROS subscriber that reports TCP drift, joint speed, torque variation,
external force, control success, and robot mode. It has no publisher or service
and is the required check before starting the command bridge.

The first Desktop network check must run `serl_ros_bridge.py --read-only`.
Read-only mode exposes ping/state but unconditionally rejects `set_pose`, so
transport and state freshness can be validated without creating a motion path.

Recorded read-only Desktop-to-NUC acceptance:

- Bridge bound to `172.16.0.2:4244` with `--read-only` and 2 cm / 0.15 rad
  safety gates; ping reported `state_ready=true, commands_enabled=false`.
- Desktop ping round trip: 0.58 ms; state request round trip: 0.49 ms.
- State age at response: 5.4 ms; TCP pose and quaternion-to-Euler conversion
  returned valid finite values.
- No `set_pose` call was made and no equilibrium target was published.

The first commanded trajectory must use `rteleop.tools.serl_step_test`. It is
hard-limited to a positive base-X move of at most 5 mm, uses a half-cosine
profile of at least one second per leg, preserves orientation, and returns to
the measured starting pose. It samples measured TCP position after every
command and reports actual X travel and cross-axis deviation. It refuses to run
unless both `--execute` and a command-enabled bridge are present.

Recorded first command test (`+base-X 1 mm`, 2 s outbound, 0.5 s hold, 2 s
return):

- Command stream completed without bridge rejection, communication failure,
  controller error, or loss of control success.
- Bridge was immediately returned to `--read-only` after the trajectory.
- Return error after 0.75 s was 0.406 mm, dominated by -0.393 mm in base Z.
- Total settled offset from the pre-test pose reached approximately 0.72 mm,
  dominated by -0.69 mm in base Z. This is consistent with static compliance
  under the unmodelled Robotiq/tool payload at the 300 N/m bring-up stiffness.
- A subsequent 10 s read-only observation drifted only 0.0285 mm total
  (-0.0280 mm Z), showing that the offset had settled rather than continuing
  to fall.
- No oscillation was measured; control success remained 1.0. Before VR use,
  evaluate payload modelling and/or a moderate stiffness increase against
  tracking latency and operator feel.

Recorded second command test (`+base-X 5 mm`, 3 s outbound, 1 s hold, 3 s
return), with measured state sampled after every command:

- Maximum measured +X travel was only 0.270 mm for a 5 mm target; maximum
  absolute Y/Z deviations were 0.079/0.597 mm. Return error was 0.656 mm.
- No bridge/controller error occurred, but 300 N/m provides only 1.5 N at the
  controller's 5 mm translational error clip and is too compliant to overcome
  the current mechanism/tool static load for useful teleoperation tracking.
- Bridge was returned to `--read-only` immediately after the trajectory.
- Post-test five-second hold drift was 0.0048 mm, torque variation remained
  low, and control success stayed 1.0: the issue is insufficient tracking
  authority, not instability.
- Do not proceed to VR at this gain. Test a moderate 600--800 N/m profile with
  matched damping and the same measured 5 mm commissioning trajectory first.

Recorded runtime reconfigure and repeat test:

- Dynamic parameters were changed online to translation 800 N/m / damping 57
  and rotation 40 Nm/rad / damping 13. Clips and nullspace settings were left
  unchanged. The generated server reported the requested values.
- Pre-motion five-second hold remained stable: 0.0014 mm TCP drift, low torque
  variation, control success 1.0, both controllers running.
- Repeating the measured 5 mm trajectory produced only 0.213 mm maximum +X
  travel, 0.058/0.431 mm Y/Z absolute peak deviation, and 0.482 mm return
  error. Increasing stiffness did not improve X tracking.
- The bridge was returned to `--read-only`; no further motion was attempted.
  Do not increase stiffness again based on these data. Instrument bridge
  acceptance, the ROS equilibrium topic, and measured state concurrently to
  locate where the target path diverges before any VR test.

`rteleop/tools/serl_trace_probe.py` is the read-only concurrent diagnostic for
that check. It subscribes to the equilibrium target and Franka state topics,
then reports target/measured XYZ spans, X-span tracking ratio, desired/measured
joint-torque spans, joint motion, and libfranka's internal desired-pose span.
It has no publisher or service.

Payload readback during the first trace showed `m_ee=0.9 kg`, end-effector COM
Z=57 mm, and no additional load. This indicates the Robotiq-class end effector
is already represented; `m_load=0` means no extra grasped payload and is not by
itself evidence of a missing gripper model.

Recorded deep trace at 800 N/m with a 5 mm target held for five seconds:

- ROS equilibrium target: exactly 5.000 mm X span, zero Y/Z span, 181 samples.
- Desired joint-torque change: 0.882 Nm maximum vector norm; largest individual
  desired-torque spans were 0.778 Nm (joint 2) and 0.619 Nm (joint 4).
- Largest joint-position span: 0.0617 degrees (joint 2).
- Measured TCP span: 0.144/0.042/0.301 mm XYZ; X tracking ratio 2.87%.
- Libfranka `O_T_EE_d` remained constant, as expected for this external torque
  controller; the SERL equilibrium target is not a libfranka Cartesian motion
  generator command.

This proves that Desktop transport, bridge publication, ROS subscription, and
the controller torque path are active. At the current pose, 800 N/m times the
5 mm clip produces approximately 4 N Cartesian authority and only a sub-degree
joint response, which is insufficient to overcome the observed static load.
Integral gain remains zero and should not be used as a shortcut because this
controller accumulates integral error without an explicit timestep factor.

Recorded 1500 N/m runtime test:

- Parameters were changed online to translation 1500 N/m / damping 77; rotation
  stayed 40 Nm/rad / damping 13, with 5 mm / 0.03 rad clips and Ki=0.
- Pre-motion hold was stable: 0.0034 mm TCP drift in five seconds, control
  success 1.0, and no errors.
- A traced 5 mm X trajectory (3 s out, 5 s hold, 3 s return) produced a 5.000
  mm target span and 1.43 mm measured X span (28.6% ratio). Y/Z measured spans
  were 0.137/0.809 mm; desired torque change was 1.50 Nm norm.
- Post-test hold remained stable: 0.0020 mm TCP drift in five seconds, control
  success 1.0, and no oscillation. The bridge was returned to read-only.
- This is an improvement over 800 N/m but not yet sufficient for VR tracking.
  Any 2000 N/m test must retain the 5 mm clip and the same trace/hold checks.

Recorded 2000 N/m runtime test:

- Parameters were changed online to translation 2000 N/m / damping 89; rotation
  stayed 40 Nm/rad / damping 13, with 5 mm / 0.03 rad clips and Ki=0.
- Pre-motion hold was stable: 0.0153 mm TCP drift in five seconds, control
  success 1.0, and no errors.
- A traced 5 mm X trajectory (3 s out, 5 s hold, 3 s return) produced a 5.000
  mm target span and 2.20 mm measured X span (43.9% ratio). Y/Z measured spans
  were 0.250/1.195 mm; desired torque change was 1.95 Nm norm.
- Post-test hold remained stable: 0.0029 mm TCP drift in five seconds, control
  success 1.0, and no oscillation. The bridge was returned to read-only.
- Increasing stiffness improves tracking monotonically but still does not
  produce full 5 mm travel. Before unrestricted VR, retain the bounded target
  and error gates and perform only a guarded clutch test.

Recorded first guarded VR clutch test:

- Quest WebXR was re-entered after restarting the Browser; the right Grip was
  confirmed as `squeeze=1` / `buttons[1]` in a read-only probe.
- With the operator at the E-stop, the bridge was briefly command-enabled and
  the Desktop app logged `clutch ENGAGED`; the operator confirmed real FR3
  motion. The app then failed closed when the bridge measured a 0.154 rad
  target/measured orientation error against the 0.150 rad limit.
- The app exited without a controller exception; the bridge was immediately
  returned to `--read-only`. A four-second post-test hold showed mode 2,
  control success 1.0, 0.0033 mm TCP drift, 0.174 deg/s maximum joint speed,
  and no oscillation.
- This validates the VR command path and the orientation safety gate, but not
  unrestricted teleoperation. Keep the 5 mm / 0.15 rad limits and resolve the
  clutch/pose procedure before another command-enabled run; do not widen the
  gate automatically.

Recorded follow-up VR play session:

- The command bridge and Desktop app were restarted with the same 2 cm / 0.15
  rad gates. The right Grip was recognized and the operator reported the
  motion felt smooth.
- The app again failed closed when a target reached 0.157 rad orientation
  error, just above the 0.150 rad limit. No controller fault was reported.
- The bridge was restored to `--read-only` after the session. A four-second
  post-session hold showed mode 2, control success 1.0, 0.0029 mm TCP drift,
  0.161 deg/s maximum joint speed, and no oscillation.
- The current limiting behavior is therefore intentional: short, smooth VR
  motions work, while hand rotation or accumulated pose lag ends the session.
  Do not widen the gate or increase stiffness without another guarded review.

Candidate bounded data-collection profile (explicitly requested, not a new
default):

- Robot-side gate: `0.03 m / 0.20 rad` (3 cm / approximately 11.5 degrees).
- Keep the existing Cartesian workspace, 0.5 s state-age watchdog, finite
  quaternion checks, and Grip/clutch behavior unchanged.
- Keep the bridge command-enabled only for a short supervised test; restore
  `--read-only` immediately afterward if the operator stops or tracking is
  abnormal. This profile does not add gripper commands; `load_gripper=false`
  and the current protocol remain pose-only.
- Do not treat this as an unrestricted profile. A later collection profile
  should add target rate/jump limiting and log accepted/rejected targets,
  measured state, clutch state, and disconnects.

Recorded guarded test of the candidate profile:

- The bridge was enabled at `0.03 m / 0.20 rad`; the Desktop app connected and
  engaged the right-Grip clutch.
- The session failed closed at `0.202 rad`, only `0.002 rad` above the
  candidate rotation gate. No controller exception was reported.
- The bridge was restored to `--read-only`. A correctly sourced four-second
  post-test hold showed mode 2, control success 1.0, `0.0023 mm` TCP drift,
  `0.178 deg/s` maximum joint speed, and no oscillation.
- The result supports testing a next bounded rotation gate, but does not
  justify removing the gate. The next candidate is `0.03 m / 0.25 rad`.

Recorded guarded test of the `0.03 m / 0.25 rad` profile:

- The command bridge was enabled with `--max-translation-error 0.03` and
  `--max-rotation-error 0.25`; the app connected and engaged the right-Grip
  clutch.
- The session again failed closed at `0.251 rad`, only `0.001 rad` above the
  configured rotation gate. This repeated the previous threshold behavior,
  rather than indicating a controller fault.
- The bridge was restored to `--read-only`. A four-second post-test hold showed
  mode 2, control success 1.0, `0.0091 mm` TCP drift, `0.161 deg/s` maximum
  joint speed, and no oscillation.
- Do not continue increasing the absolute rotation gate alone. The next data
  profile should pair a wider total-error bound with an explicit per-command
  target step/rate limit.

Implemented bounded data-collection bridge profile:

- `--max-translation-error 0.03`
- `--max-rotation-error 0.35`
- `--max-rotation-step 0.04`
- The step limiter interpolates each accepted quaternion target from the last
  accepted target (or the measured pose after one second idle) along the
  shortest arc. The resulting target is rechecked against the measured-state
  safety gate before publication.
- The default bridge behavior is unchanged when the optional step arguments
  are omitted. The profile must remain a supervised, command-enabled test;
  return to `--read-only` after the session.
- After the first `.35 rad` dry deployment exposed an ordering bug, the bridge
  was corrected to apply the optional rotation step before the final measured
  orientation check. The corrected file passed 25 local tests and was loaded
  on NUC in read-only mode; no controller source was changed.

Recorded corrected-profile VR test (fresh run):

- The bridge ran with `0.03 m / 0.35 rad` total gates and `0.04 rad` rotation
  steps; the app connected and engaged the right-Grip clutch.
- The session stopped safely at a final measured orientation error of
  `0.355 rad`. This confirms that the step limiter is not a substitute for the
  configured total orientation range.
- The bridge was restored to `--read-only`. A four-second post-test hold showed
  mode 2, control success 1.0, `0.0213 mm` TCP drift, `0.222 deg/s` maximum
  joint speed, and no oscillation.
- For longer demonstrations, increase only the total rotation bound in a new
  supervised profile while retaining the `0.04 rad` step limiter; do not
  remove the step or workspace/state gates.

Final web-controller handoff (2026-09-01):

- On operator request, the read-only bridge, `roslaunch`, `franka_control`, and
  both SERL controller spawners were stopped with their targeted process IDs.
- `remote-teleop-serl-soft` was left as an idle `sleep infinity` container;
  there are no ROS or bridge processes inside it, so FCI is released.
- `droid-nuc-fr3` was not started or modified. The web/Desk controller can now
  be used to return the FR3 to home.

## Improvements before production collection

The implementation is at supervised bring-up level. The following items are
the concrete backlog before collecting large demonstration sets:

1. **Separate range from continuity.** The current candidate (`0.03 m`,
   `0.35 rad` total, `0.04 rad` rotation step) still stops when the total
   orientation range is reached. Choose the range from the task, add XYZ
   target rate/acceleration limits, and retain workspace plus state-age checks.
2. **Record synchronized demonstrations.** Store raw WebXR pose, accepted
   target, measured TCP/joints, monotonic/wall timestamps, clutch state, bridge
   rejection reason, disconnects, and A/B labels in one versioned format.
3. **Make expected stops clean.** A bridge rejection currently fails closed but
   prints a traceback. Return a structured session status, write the reason to
   the recording, and restore the bridge to read-only automatically.
4. **Automate FCI lifecycle.** Add a preflight/start/stop wrapper that checks
   DROID/SERL exclusivity, starts read-only first, checks controller health,
   and performs deterministic cleanup without broad remote `pkill` commands.
5. **Calibrate robot tracking.** The live 2000 N/m profile tracked only 2.20 mm
   of a 5 mm X target and showed static tool/payload compliance. Recheck TCP,
   Robotiq mass/COM, collision thresholds, and stiffness/damping using the
   trace/hold probes; do not use integral gain as an unvalidated shortcut.
6. **Implement Robotiq safely.** The current `load_gripper=false` pose-only
   bridge intentionally ignores the large index trigger. Add a separate
   bounded gripper channel with timeout, deadman, collision-aware limits, and
   hardware-free protocol tests.
7. **Improve orientation UX.** Add an explicit rotate/lock-orientation mode,
   show the active range in the operator UI, and calibrate the WebXR axis map
   and orientation gain. Current rotation is controller twist/tilt, not a
   thumbstick action.
8. **Measure the complete loop.** Instrument WebXR receive, Desktop target
   send, ROS equilibrium publication, and measured FR3 state to obtain the
   SERL pose-to-motion latency and data-quality metrics before scaling up.
9. **Harden the XR deployment.** Package `three.module.js` and static assets
   with the source-of-truth checkout, expose controller/pose health in the
   Quest page, and test stale-session recovery rather than relying on Browser
   restarts.

## NUC parameter snapshot (read-only, after the tests)

The following values were read from the live ROS master and Franka state; no
parameters were changed by the readback:

- Launch: `robot_ip:=172.16.0.1`, `load_gripper:=false`; active controllers are
  `franka_state_controller` and `cartesian_impedance_controller`, both
  `running`.
- Cartesian compliance: translation `2000 N/m`, damping `89`; rotation
  `40 Nm/rad`, damping `13`; nullspace `0.2`; joint-1 nullspace `10`;
  translational clip `±0.005 m`; rotational clip `±0.03 rad`; both Ki values
  `0`.
- Robot-side bridge: bound `172.16.0.2:4244`, gate `0.02 m / 0.15 rad`,
  `--read-only`; no target commands accepted.
- Collision thresholds: nominal and acceleration force thresholds
  `[20,20,20,25,25,25]`; joint torque thresholds
  `[20,20,18,18,16,14,12]` (same upper/lower values).
- Live payload: `m_ee=0.9 kg`, `F_x_Cee=[0,0,0.057] m`, `m_load=0`, hence
  `m_total=0.9 kg` with no additional grasped payload.
- Live state at readback: robot mode `2`, control success `1.0`, no current or
  last-motion errors.

These are runtime values, not necessarily the source defaults. The source
defaults and isolated-image patches remain recorded separately above.

Recorded first soft hold takeover (no bridge, no target publication):

- Both `franka_state_controller` and `cartesian_impedance_controller` running.
- `control_command_success_rate=1.0`, robot mode 2, no current/last motion
  errors, and no ROS WARN/ERROR/FATAL entries.
- State publication approximately 29.4 Hz.
- Five-second probe: TCP drift 0.0017 mm, maximum deviation 0.0031 mm; measured
  torque variation 0.064 Nm RMS / 0.114 Nm maximum; desired torque variation
  0.020 Nm RMS / 0.040 Nm maximum; external force approximately 1.70 N RMS.
- Operator reported a steady normal operating sound, not high-frequency and
  not periodic; no visible motion. Data did not indicate sag or oscillation.

## Real-hardware boundary (do not cross unattended)

Before switching controllers: operator at the arm with E-stop in hand, clear
workspace, and no one relying on the existing DROID service. Follow the local
Desk procedure to keep the FR3 secured while switching controllers; unlock the
brakes only after controller/state checks are healthy and immediately before a
guarded motion test. FCI is exclusive. Stop `droid-nuc-fr3` before launching
ROS; never run both robot clients.

### Required controller fix

The checked controller source leaves `Ki_` and `error_i` uninitialized before
the 1 kHz update loop reads them. Eigen fixed-size matrices do not zero
themselves. Build the isolated image in
[`deploy/serl`](../deploy/serl/README.md); it applies
[the local initialization patch](../patches/serl_franka_controllers-initialize-integrator.patch)
and rebuilds the package in a derived image without modifying the NUC's RLinf
checkout or base image. Do not rely on the current binary merely because a
previous run happened to start cleanly.

Inside `rlinf-explore`, source the built catkin workspace and launch:

```bash
source /opt/venv/franka-0.18.0/franka_catkin_ws/devel/setup.bash
roslaunch serl_franka_controllers impedance.launch \
  robot_ip:=172.16.0.1 load_gripper:=false
```

SERL initializes its equilibrium pose from the measured pose. Its checked
defaults are 2000 N/m translation, 150 Nm/rad rotation, and 1 cm/50 mrad error
clipping. First validate state/controller topics with no target publication.

Copy `rteleop/tools/serl_ros_bridge.py` into the container and run it only
after the controller is healthy:

```bash
python3 serl_ros_bridge.py --bind 172.16.0.2 --port 4244 \
  --max-translation-error 0.02 --max-rotation-error 0.15
```

Start with tighter 2 cm / 0.15 rad robot-side gates. On the Desktop, validate
`ping` and state reads before starting the Quest app. The final teleop entry is:

```bash
source .venv/bin/activate
python -m rteleop.apps.serl_teleop_fr3 --pose-source webxr --hz 30
```

On clutch release or network disconnect no new target is published; SERL holds
the last accepted target. The bridge rejects stale robot state, distant poses,
workspace violations, and large orientation errors.

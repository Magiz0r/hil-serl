"""Real online command loop against synthetic ROS, with network disabled."""
import json
from pathlib import Path
import subprocess
import sys

import offline_recovery_smoke as recovery
fake=recovery.fake
fake.require_offline()


def online_plan():
    import numpy as np
    p=recovery.plan();initial=np.asarray(p['poses_xyz_quaternion'][0])
    return dict(schema='autoserl_online_plan_v1',initial_pose=initial.tolist(),custom_home_q=fake.Q,
        workspace_low=(initial[:3]-.05).tolist(),workspace_high=(initial[:3]+.05).tolist(),
        gripper_position=130,force_limit_N=10.,torque_limit_Nm=1.,max_steps=300)


def scenario(kind):
    process=subprocess.Popen([sys.executable,__file__,'--trial'],stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    count=0;result=None;index=0;interrupted=False
    try:
        for line in process.stdout:
            message=json.loads(line)
            if message['phase']=='stopped':result=message;break
            if message['phase']=='stopping' or process.stdin.closed:continue
            if message['phase']=='ready':count+=1
            online=message.get('pilot',{}).get('online',{})
            phase=online.get('phase')
            if online.get('completed_index',-1)>=index:index+=1
            interrupt=index>=5
            if interrupt and kind=='disconnect':process.stdin.close();continue
            if interrupt and kind=='lock':interrupted=True
            command='lock' if count<5 or interrupted else 'policy_start'
            cid=2 if interrupted else 0 if count<5 else 1
            pilot=dict(id=cid,action=command,connected=True)
            if command=='policy_start':
                pilot['policy']=dict(index=index,action=[0.,0.,.03,0.,0.,0.],
                    age=.5 if interrupt and kind=='stale' else 0.,
                    buttons=[bool(interrupt and kind=='complete'),False])
            packet=dict(seq=count+int(message['server_time']*1000000),server_time=message['server_time'],
                axes=[0.]*6,enable=False,stop=phase=='ended' or count>1000,pilot=pilot)
            process.stdin.write(json.dumps(packet)+'\n');process.stdin.flush()
        code=process.wait(timeout=10)
        assert result,process.stderr.read()
        assert code==(1 if kind=='disconnect' else 0),result
        assert result['pilot']['online']['phase']=='ended',result
        assert 'cleanup_error' not in result and 'recovery_log_error' not in result,result
        run=Path(result['run_directory'])
        rows=[json.loads(x) for x in (run/'online-actions.jsonl').read_text().splitlines()]
        actions=[r for r in rows if r.get('kind')!='terminal']
        assert len(actions)>=5 and [r['index'] for r in actions]==list(range(len(actions)))
        assert (run/'demo-actions.jsonl').read_text()==''
        assert rows[-1]['kind']=='terminal'
        assert rows[-1]['operator_success']==(kind=='complete')
        print(json.dumps(dict(scenario=kind,passed=True,action_count=len(actions),
            terminal=rows[-1]['reason'],run=str(run))),flush=True)
    finally:
        if not process.stdin.closed:process.stdin.close()
        if process.poll() is None:process.terminate();process.wait(timeout=10)


if __name__=='__main__':
    if '--robot' in sys.argv:fake.synthetic_robot()
    elif '--trial' in sys.argv:
        import upward_trial
        from custom_home import CustomHomeStore
        from fr3_kinematics import chain_from_urdf
        import roslaunch
        cfg=roslaunch.config.ROSLaunchConfig()
        roslaunch.xmlloader.XmlLoader().load(str(fake.ROOT/'fr3_hold.launch'),cfg,
            argv=['robot_ip:=172.16.0.1'],verbose=False)
        _,lower,upper=chain_from_urdf(cfg.params['/robot_description'].value)
        CustomHomeStore('/hil-serl-state/custom_home.json').save(fake.Q,lower,upper)
        upward_trial.recovery_plan=recovery.plan
        upward_trial.online_plan=online_plan
        sys.exit(fake.synthetic_trial())
    else:scenario(sys.argv[sys.argv.index('--scenario')+1])

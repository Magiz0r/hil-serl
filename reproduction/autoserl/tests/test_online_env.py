from unittest.mock import Mock
import time

import pytest

from reproduction.autoserl.bootstrap import ROOT
from reproduction.autoserl.online_env import PortalEnv
from reproduction.autoserl.online_smoke import SyntheticPortal


def test_episode_cleanup_does_not_stop_subsequent_operator_home(tmp_path):
    transport = SyntheticPortal(ROOT, tmp_path)
    transport.stop = Mock(wraps=transport.stop)
    env = PortalEnv(ROOT, transport)
    env.close()  # Preparing/cancelling an unused environment sends no Stop.
    transport.stop.assert_not_called()

    env.reset()
    # The controller ends the episode on the operator's success button.
    transport.count = 119
    _, reward, done, _, _ = env.step([0.] * 6)
    assert done and reward == 1 and not env.active
    env.close()  # Terminal actions still get their first cleanup.
    transport.stop.assert_called_once()

    # Operator starts Home while the runner waits for the final label.
    transport.motion.mode = 'homing'
    env.close()  # Evaluation finalizer must not issue a second lock.
    transport.stop.assert_called_once()
    assert transport.motion.mode == 'homing'

    transport.motion.mode = 'locked'
    transport.cid += 1  # The portal assigns a new command ID at each start.
    env.reset()
    assert env.active
    env.close()  # A new episode restores the obligation to stop.
    assert transport.stop.call_count == 2 and not env.active
    assert transport.motion.mode == 'locked'


def test_uncertain_start_and_failed_stop_keep_cleanup_retryable():
    transport = Mock()
    transport.request.side_effect = RuntimeError('start acknowledgement lost')
    transport.stop.side_effect = [RuntimeError('stop acknowledgement lost'), None]
    env = PortalEnv(ROOT, transport)
    with pytest.raises(RuntimeError, match='start acknowledgement'):
        env.reset()
    with pytest.raises(RuntimeError, match='stop acknowledgement'):
        env.close()
    env.close()
    env.close()
    assert transport.stop.call_count == 2


@pytest.mark.parametrize('seconds,success',[(0,False),(60,False),(60,True)])
def test_user_duration_applies_at_reset_and_preserves_success_priority(tmp_path,monkeypatch,seconds,success):
    setting={'time_limit_seconds':seconds}
    monkeypatch.setattr('reproduction.autoserl.online_env.training_settings',lambda *_:dict(setting))
    transport=SyntheticPortal(ROOT,tmp_path);env=PortalEnv(ROOT,transport)
    transport.stop=Mock(wraps=transport.stop)
    env.reset();env.episode_started=time.monotonic()-61.
    setting['time_limit_seconds']=1  # Changing the saved setting cannot alter this episode.
    if success:transport.count=119
    _,reward,done,truncated,info=env.step([0.]*6)
    assert env.episode_time_limit_seconds==seconds
    assert reward==float(success) and done==success
    assert truncated==bool(seconds and not success)
    if seconds and not success:
        assert info['terminal_reason']=='time_limit' and not info['operator_abort']
        assert not env.active and transport.motion.mode=='locked'
        env.close();transport.stop.assert_called_once()
    elif not success:
        assert env.active and info['terminal_reason'] is None
        transport.stop.assert_not_called()

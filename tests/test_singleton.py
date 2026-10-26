import pytest

from common.paths import data_paths
from hub.singleton import AlreadyRunning, InstanceLock


def test_second_lock_is_refused_until_the_first_is_released():
    path = data_paths().ensure().hub_lock
    with InstanceLock(path), pytest.raises(AlreadyRunning):
        InstanceLock(path).acquire()
    with InstanceLock(path):  # free again
        pass

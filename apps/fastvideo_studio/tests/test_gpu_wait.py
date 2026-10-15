# SPDX-License-Identifier: Apache-2.0
import pytest

from fastvideo_studio import gpu_wait


def test_idle_selection_requires_free_memory_low_load_and_no_compute_process():
    rows = gpu_wait.parse_gpus('0, GPU-a, 48510, 0, 0\n1, GPU-b, 48000, 510, 0\n2, GPU-c, 48000, 510, 80\n')
    assert gpu_wait.idle_indices(rows, {'GPU-b'}, 45000) == [0]
    assert gpu_wait.idle_indices(rows, set(), 45000) == [0, 1]


def test_invalid_gpu_measurement_does_not_become_idle():
    with pytest.raises(ValueError):
        gpu_wait.parse_gpus('0, GPU-a, N/A, 0, 0')


def test_wait_requires_consecutive_identical_free_gpu_samples():
    readings = iter([[1], [], [2], [2], [2]])
    waits = []
    selected = gpu_wait.wait_for_idle(lambda: next(readings), count=1, stable_checks=3,
                                     interval=0, timeout=5, sleep=waits.append)
    assert selected == [2]
    assert len(waits) == 4


def test_wait_timeout_never_launches_on_busy_cards():
    with pytest.raises(TimeoutError):
        gpu_wait.wait_for_idle(lambda: [], count=1, stable_checks=2, interval=.01, timeout=.02)

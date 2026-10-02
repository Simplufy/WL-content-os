from studio import db, jobs


def test_enqueue_dedupes_and_claims_by_priority():
    a = jobs.enqueue("check_creator", 1)
    assert jobs.enqueue("check_creator", 1) is None
    b = jobs.enqueue("process_video", 7, priority=10)
    job = jobs.claim(["check_creator", "process_video"])
    assert job["id"] == b and job["attempts"] == 1
    assert jobs.claim(["process_video"]) is None
    assert jobs.claim(["check_creator"])["id"] == a


def test_fail_retries_then_gives_up():
    jobs.enqueue("process_video", 1, max_attempts=2)
    j = jobs.claim(["process_video"])
    assert jobs.fail(j, "boom", retry_delay_s=0) is True
    j = jobs.claim(["process_video"])
    assert j["attempts"] == 2
    assert jobs.fail(j, "boom again") is False
    assert db.row("SELECT status FROM jobs WHERE id=?", (j["id"],))["status"] == "failed"


def test_postpone_keeps_attempts():
    jobs.enqueue("analyze_video", 3)
    j = jobs.claim(["analyze_video"])
    jobs.postpone(j, 0, "no login")
    assert db.row("SELECT attempts, status FROM jobs WHERE id=?", (j["id"],)) == {"attempts": 0, "status": "pending"}


def test_recover_stale():
    jobs.enqueue("check_creator", 1)
    jobs.claim(["check_creator"])
    assert jobs.recover_stale() == 1
    assert jobs.claim(["check_creator"]) is not None

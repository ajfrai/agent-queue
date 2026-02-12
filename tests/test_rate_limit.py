"""Test harness for rate limit detection.

Tests the rate limit monitor's ability to:
1. Run a probe against the real Claude CLI
2. Parse rate limit error messages
3. Extract reset times from various formats
4. Detect rate limits from CLI output
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_parse_reset_time():
    """Test reset time parsing from various error message formats."""
    from agent_queue.core.rate_limit_monitor import RateLimitMonitor

    monitor = RateLimitMonitor()

    # Format: "resets 8pm (America/New_York)"
    result = monitor._parse_reset_time(
        "You've hit your limit · resets 8pm (America/New_York)"
    )
    assert result is not None, "Failed to parse 'resets 8pm (America/New_York)'"
    print(f"  PASS: Parsed '8pm (timezone)' -> {result.isoformat()}")

    # Format: "resets 10:30pm (America/New_York)"
    result = monitor._parse_reset_time(
        "You've hit your limit · resets 10:30pm (America/New_York)"
    )
    assert result is not None, "Failed to parse 'resets 10:30pm'"
    print(f"  PASS: Parsed '10:30pm (timezone)' -> {result.isoformat()}")

    # Format: "try again in 30 minutes"
    result = monitor._parse_reset_time("Rate limited. Try again in 30 minutes.")
    assert result is not None, "Failed to parse 'try again in 30 minutes'"
    print(f"  PASS: Parsed 'try again in 30 minutes' -> {result.isoformat()}")

    # Format: "try again in 2 hours"
    result = monitor._parse_reset_time("Rate limited. Try again in 2 hours.")
    assert result is not None, "Failed to parse 'try again in 2 hours'"
    print(f"  PASS: Parsed 'try again in 2 hours' -> {result.isoformat()}")

    # No reset info - should return default (1 hour from now)
    result = monitor._parse_reset_time("Some error with no reset info")
    assert result is not None, "Should return default reset time"
    print(f"  PASS: Default fallback -> {result.isoformat()}")

    print("All reset time parsing tests passed!")


def test_detect_rate_limit():
    """Test rate limit detection from various text patterns."""
    from agent_queue.core.rate_limit_monitor import RateLimitMonitor

    monitor = RateLimitMonitor()

    # Should detect
    positives = [
        "You've hit your limit · resets 8pm (America/New_York)",
        "Error: rate limit exceeded",
        "Too many requests, please try again later",
        "You have exceeded your usage limit",
        "API rate limit reached",
    ]

    for text in positives:
        assert monitor._detect_rate_limit(text), f"Should detect: {text[:50]}"
        print(f"  PASS: Detected rate limit in: {text[:60]}")

    # Should NOT detect
    negatives = [
        "Hello, how can I help you?",
        "Task completed successfully",
        '{"type": "result", "is_error": false}',
        "ok",
    ]

    for text in negatives:
        assert not monitor._detect_rate_limit(text), f"Should NOT detect: {text[:50]}"
        print(f"  PASS: Correctly ignored: {text[:60]}")

    print("All rate limit detection tests passed!")


def test_interpret_probe_success():
    """Test interpretation of a successful probe."""
    from agent_queue.core.rate_limit_monitor import RateLimitMonitor

    monitor = RateLimitMonitor()

    # Simulate successful probe
    result = {
        "success": True,
        "json_output": {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "ok",
            "total_cost_usd": 0.001,
        },
        "stderr": "",
        "exit_code": 0,
        "raw_output": '{"type":"result","result":"ok"}',
    }

    status = monitor._interpret_probe_result(result)
    assert not status.is_limited, "Successful probe should not be rate limited"
    print(f"  PASS: Successful probe -> is_limited={status.is_limited}")
    print("Probe success interpretation test passed!")


def test_interpret_probe_rate_limited():
    """Test interpretation of a rate-limited probe."""
    from agent_queue.core.rate_limit_monitor import RateLimitMonitor

    monitor = RateLimitMonitor()

    # Simulate rate-limited probe (stderr output)
    result = {
        "success": False,
        "json_output": None,
        "stderr": "You've hit your limit · resets 8pm (America/New_York)",
        "exit_code": 1,
        "raw_output": "\nYou've hit your limit · resets 8pm (America/New_York)",
    }

    status = monitor._interpret_probe_result(result)
    assert status.is_limited, "Rate limited probe should be detected"
    assert status.reset_at is not None, "Should parse reset time"
    print(f"  PASS: Rate limited probe -> is_limited={status.is_limited}, reset_at={status.reset_at}")

    # Simulate rate-limited probe (JSON error response)
    result2 = {
        "success": True,
        "json_output": {
            "type": "result",
            "is_error": True,
            "result": "You've hit your limit · resets 8pm (America/New_York)",
        },
        "stderr": "",
        "exit_code": 0,
        "raw_output": '{"type":"result","is_error":true,"result":"You\'ve hit your limit"}',
    }

    status2 = monitor._interpret_probe_result(result2)
    assert status2.is_limited, "JSON error probe should be detected"
    print(f"  PASS: JSON error probe -> is_limited={status2.is_limited}")

    print("Probe rate limit interpretation tests passed!")


async def test_live_probe():
    """Test a real probe against the Claude CLI (integration test)."""
    from agent_queue.core.rate_limit_monitor import RateLimitMonitor

    monitor = RateLimitMonitor()

    print("\nRunning live probe against Claude CLI...")
    result = await monitor._run_probe()

    print(f"  Exit code: {result['exit_code']}")
    print(f"  Success: {result['success']}")
    print(f"  Stderr: {result['stderr'][:200] if result['stderr'] else '(empty)'}")

    if result['json_output']:
        json_out = result['json_output']
        print(f"  JSON type: {json_out.get('type')}")
        print(f"  Is error: {json_out.get('is_error')}")
        print(f"  Result: {str(json_out.get('result', ''))[:100]}")
        print(f"  Cost: ${json_out.get('total_cost_usd', 0):.6f}")
    else:
        print(f"  Raw output: {result['raw_output'][:200]}")

    # Interpret the result
    status = monitor._interpret_probe_result(result)
    print(f"\n  Rate limited: {status.is_limited}")
    print(f"  Tier: {status.tier}")
    if status.reset_at:
        print(f"  Reset at: {status.reset_at.isoformat()}")

    return status


def main():
    """Run all tests."""
    print("=" * 60)
    print("Rate Limit Detection Test Harness")
    print("=" * 60)

    print("\n--- Test: Reset Time Parsing ---")
    test_parse_reset_time()

    print("\n--- Test: Rate Limit Detection ---")
    test_detect_rate_limit()

    print("\n--- Test: Probe Success Interpretation ---")
    test_interpret_probe_success()

    print("\n--- Test: Probe Rate Limited Interpretation ---")
    test_interpret_probe_rate_limited()

    print("\n--- Test: Live Probe (Integration) ---")
    status = asyncio.run(test_live_probe())

    print("\n" + "=" * 60)
    if status.is_limited:
        print(f"RESULT: Currently RATE LIMITED")
        if status.reset_at:
            print(f"  Resets at: {status.reset_at.isoformat()}")
    else:
        print(f"RESULT: Capacity AVAILABLE")
    print("=" * 60)

    print("\nAll tests passed!")


if __name__ == "__main__":
    main()

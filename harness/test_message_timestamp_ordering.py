from storage.message_store import _message_sort_key


def test_equal_legacy_message_timestamps_keep_user_before_assistant():
    timestamp = "2026-08-18T06:16:51.661472+00:00"
    messages = [
        {
            "message_id": "fc148697:assistant",
            "role": "assistant",
            "created_at": timestamp,
            "run_id": "fc148697",
        },
        {
            "message_id": "request_41:user",
            "role": "user",
            "created_at": timestamp,
            "run_id": "request_41",
        },
    ]

    ordered = sorted(messages, key=_message_sort_key)

    assert [message["role"] for message in ordered] == ["user", "assistant"]

from launchloop.eventbrite import BoundedEventbriteReader


class StubReader(BoundedEventbriteReader):
    def __init__(self) -> None:
        self.urls: list[str] = []

    def _get_json(self, url: str, token: str) -> dict[str, object]:
        self.urls.append(url)
        assert token == "synthetic-token"
        if "organizations" in url and "events" not in url:
            return {
                "organizations": [{"id": "42"}],
                "pagination": {"has_more_items": False},
            }
        return {
            "events": [
                {
                    "id": "123",
                    "name": {"text": "Community Forum"},
                    "status": "draft",
                    "changed": "2026-08-31T12:00:00Z",
                    "start": {"utc": "2026-09-20T14:00:00Z", "timezone": "America/Toronto"},
                    "end": {"utc": "2026-09-20T16:00:00Z"},
                    "description": {"text": "unsafe provider text"},
                    "attendees": [{"email": "private@example.test"}],
                }
            ],
            "pagination": {"has_more_items": False},
        }


def test_adapter_retains_only_allowlisted_metadata_and_uses_list_endpoints() -> None:
    reader = StubReader()

    events = reader._list_with_credential(memoryview(b"synthetic-token"))

    assert events[0].title == "Community Forum"
    assert not hasattr(events[0], "description")
    assert not hasattr(events[0], "attendees")
    assert all("page_size=" in url for url in reader.urls)
    event_list_url = next(url for url in reader.urls if "/events/" in url)
    assert "status=draft%2Clive" in event_list_url
    assert "ended" not in event_list_url
    assert "completed" not in event_list_url
    assert "canceled" not in event_list_url

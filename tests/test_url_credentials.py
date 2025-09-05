"""Test URL credential normalization across streams."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.url import normalize_stream_url


def test_normalize_stream_url_encodes_credentials():
    url = "rtsp://user name:p@ss@example.com/stream"
    expected = "rtsp://user%20name:p%40ss@example.com/stream"
    assert normalize_stream_url(url) == expected

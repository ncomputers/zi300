from config import CONFIG_DEFAULTS
from config.storage import _apply_defaults, _rewrite_pipelines


def test_apply_defaults_populates_and_normalizes():
    data = {
        "track_ppe": ["helmet", "NO_VEST_JACKET"],
        "backend_priority": ["opencv"],
    }
    result = _apply_defaults(data)
    assert result["frame_skip"] == CONFIG_DEFAULTS["frame_skip"]
    assert result["track_ppe"] == ["helmet", "vest_jacket"]
    assert result["stream_mode"] == "ffmpeg"
    assert result["backend_priority"] == ["ffmpeg", "opencv"]


def test_rewrite_pipelines_converts_legacy_fields():
    cfg = {"pipeline_profiles": {"cam": {"extra_pipeline": "foo", "ffmpeg_flags": "-bar"}}}
    _rewrite_pipelines(cfg)
    profile = cfg["pipeline_profiles"]["cam"]
    pipes = profile["pipelines"]
    assert "-bar" in pipes["ffmpeg"]
    assert pipes["opencv"] == "{url}"
    assert "extra_pipeline" not in profile
    assert "ffmpeg_flags" not in profile

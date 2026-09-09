"""Phase 28 focused tests: FFmpegCommandBuilder
(app/video_encoder/encoder.py).

Pure command-construction tests -- ffmpeg is never actually invoked here.
"""

from __future__ import annotations

from app.models.common import MusicState
from app.video_encoder.audio_mix import AudioMixPlan, MusicRegionPlan, SfxEventPlan
from app.video_encoder.encoder import FFmpegCommandBuilder
from app.video_encoder.models import (
    AudioAssetBindings,
    VideoEncodeRequest,
    VideoEncodingSettings,
    VideoNarrationClip,
    VideoSegmentInput,
)


def _settings(**overrides) -> VideoEncodingSettings:
    fields = dict(width=1920, height=1080, fps=30)
    fields.update(overrides)
    return VideoEncodingSettings(**fields)


def _segment(**overrides) -> VideoSegmentInput:
    fields = dict(
        segment_id="S1", image_path="a.png", duration_ms=1000,
        narration_clips=[VideoNarrationClip(file_path="a.wav")],
        transition_in="HOLD", transition_out="CUT",
    )
    fields.update(overrides)
    return VideoSegmentInput(**fields)


def _request(segments, **overrides) -> VideoEncodeRequest:
    fields = dict(segments=segments, settings=_settings(), output_path="out.mp4")
    fields.update(overrides)
    return VideoEncodeRequest(**fields)


def _build(request, audio_mix_plan=None) -> list[str]:
    return FFmpegCommandBuilder.build(request, "ffmpeg", audio_mix_plan)


def _music_region(start_ms, end_ms, gain_db=-24.0, state=MusicState.BED) -> MusicRegionPlan:
    return MusicRegionPlan(start_ms=start_ms, end_ms=end_ms, gain_db=gain_db, state=state)


def _sfx_event(timestamp_ms, sfx_id, file_path) -> SfxEventPlan:
    return SfxEventPlan(timestamp_ms=timestamp_ms, sfx_id=sfx_id, file_path=file_path)


# ---------------------------------------------------------------------------
# One / three segments
# ---------------------------------------------------------------------------


def test_one_segment_command_shape():
    request = _request([_segment()])
    command = _build(request)

    assert command[0] == "ffmpeg"
    assert "-y" in command
    assert command.count("-i") == 2  # one image input, one narration input
    assert "a.png" in command
    assert "a.wav" in command
    assert command[-1] == "out.mp4"


def test_three_segments_command_has_three_image_inputs():
    segments = [
        _segment(segment_id="S1", image_path="a.png", narration_clips=[VideoNarrationClip(file_path="a.wav")]),
        _segment(segment_id="S2", image_path="b.png", narration_clips=[VideoNarrationClip(file_path="b.wav")]),
        _segment(segment_id="S3", image_path="c.png", narration_clips=[VideoNarrationClip(file_path="c.wav")]),
    ]
    request = _request(segments)
    command = _build(request)

    assert command.count("-loop") == 3
    for image in ("a.png", "b.png", "c.png"):
        assert image in command
    for audio in ("a.wav", "b.wav", "c.wav"):
        assert audio in command


# ---------------------------------------------------------------------------
# Repeated visual / multiple narration refs
# ---------------------------------------------------------------------------


def test_repeated_visual_produces_two_separate_loop_inputs():
    """Reusing the same image across segments must not deduplicate the
    ffmpeg input -- each segment still gets its own -loop input so its own
    exact duration boundary is preserved (Phase 28 requirement #10)."""
    segments = [
        _segment(segment_id="S1", image_path="shared.png", duration_ms=1000),
        _segment(segment_id="S2", image_path="shared.png", duration_ms=2000),
    ]
    request = _request(segments)
    command = _build(request)

    assert command.count("shared.png") == 2
    assert command.count("-loop") == 2


def test_multiple_narration_refs_in_one_segment():
    segment = _segment(
        narration_clips=[VideoNarrationClip(file_path="a1.wav"), VideoNarrationClip(file_path="a2.wav")]
    )
    request = _request([segment])
    command = _build(request)

    assert "a1.wav" in command and "a2.wav" in command
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "concat=n=2:v=0:a=1" in filter_complex  # the 2-clip narration concat
    assert "[0:a]" in filter_complex or "[1:a]" in filter_complex  # audio input refs


# ---------------------------------------------------------------------------
# Paths with spaces / Windows-style paths
# ---------------------------------------------------------------------------


def test_paths_with_spaces_preserved_as_single_argv_entries():
    from pathlib import Path

    image_path, audio_path, output_path = (
        str(Path("my folder/a image.png")),
        str(Path("my folder/a audio.wav")),
        str(Path("my folder/out.mp4")),
    )
    segment = _segment(image_path=image_path, narration_clips=[VideoNarrationClip(file_path=audio_path)])
    request = _request([segment], output_path=output_path)
    command = _build(request)

    assert image_path in command
    assert audio_path in command
    assert command[-1] == output_path
    # Never a shell-joined string -- every path is its own argv element.
    assert not any(
        " " in arg and arg not in (image_path, audio_path, output_path)
        for arg in command if isinstance(arg, str)
    )


def test_windows_style_paths_preserved():
    segment = _segment(
        image_path=r"C:\videos\a.png", narration_clips=[VideoNarrationClip(file_path=r"C:\videos\a.wav")]
    )
    request = _request([segment], output_path=r"C:\videos\out.mp4")
    command = _build(request)

    assert any("a.png" in arg for arg in command)
    assert any("a.wav" in arg for arg in command)
    assert command[-1].endswith("out.mp4")


# ---------------------------------------------------------------------------
# fps / codec / pixel format / duration
# ---------------------------------------------------------------------------


def test_exact_30fps_configuration():
    request = _request([_segment()], settings=_settings(fps=30))
    command = _build(request)
    assert "-r" in command
    assert command[command.index("-r") + 1] == "30"


def test_h264_aac_codec_selection():
    request = _request([_segment()])
    command = _build(request)
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "aac"


def test_yuv420p_pixel_format():
    request = _request([_segment()])
    command = _build(request)
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"


def test_final_duration_bounded_to_total_timeline_duration():
    segments = [_segment(segment_id="S1", duration_ms=1000), _segment(segment_id="S2", duration_ms=1500)]
    request = _request(segments)
    command = _build(request)

    # The LAST -t flag (after -filter_complex) bounds total output duration.
    t_indices = [i for i, arg in enumerate(command) if arg == "-t"]
    last_t_value = command[t_indices[-1] + 1]
    assert last_t_value == "2.500"


def test_custom_fps_and_codec_reflected():
    request = _request(
        [_segment()],
        settings=_settings(fps=24, video_codec="libx265", audio_codec="mp3", pixel_format="yuv444p"),
    )
    command = _build(request)
    assert command[command.index("-r") + 1] == "24"
    assert command[command.index("-c:v") + 1] == "libx265"
    assert command[command.index("-c:a") + 1] == "mp3"
    assert command[command.index("-pix_fmt") + 1] == "yuv444p"


# ---------------------------------------------------------------------------
# No shell invocation / map arguments
# ---------------------------------------------------------------------------


def test_command_is_argument_list_not_shell_string():
    request = _request([_segment()])
    command = _build(request)
    assert isinstance(command, list)
    assert all(isinstance(arg, str) for arg in command)


def test_map_arguments_reference_named_filter_outputs():
    request = _request([_segment()])
    command = _build(request)
    map_indices = [i for i, arg in enumerate(command) if arg == "-map"]
    mapped = [command[i + 1] for i in map_indices]
    assert "[vout]" in mapped
    assert "[aout]" in mapped


def test_determinism_identical_request_produces_identical_command():
    segments = [_segment(segment_id="S1"), _segment(segment_id="S2", image_path="b.png")]
    request1 = _request(segments)
    request2 = _request(
        [
            _segment(segment_id="S1"),
            _segment(segment_id="S2", image_path="b.png"),
        ]
    )
    assert _build(request1) == _build(request2)


# ---------------------------------------------------------------------------
# Phase 29: CROSSFADE timing/offsets
# ---------------------------------------------------------------------------


def test_two_segment_crossfade_uses_xfade_with_extended_first_input():
    segments = [
        _segment(segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
        _segment(segment_id="S2", image_path="b.png", duration_ms=1500, transition_in="CUT", transition_out="CUT"),
    ]
    request = _request(segments, settings=_settings(crossfade_duration_ms=300))
    command = _build(request)

    # S1's own -t is extended by the crossfade duration (1000+300=1300ms);
    # S2's is untouched.
    t_values = [command[i + 1] for i, arg in enumerate(command) if arg == "-t"]
    assert t_values[0] == "1.300"
    assert t_values[1] == "1.500"

    filter_complex = command[command.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.300:offset=1.000[vout]" in filter_complex
    # No flat concat anywhere in the video chain when a crossfade exists.
    assert "concat=n=2:v=1:a=0" not in filter_complex

    # The LAST -t (bounding total output duration) is the naive sum --
    # duration-neutral despite the crossfade.
    last_t = command[len(command) - 1 - command[::-1].index("-t") + 1]
    assert last_t == "2.500"


def test_three_segments_mixed_cut_then_crossfade():
    segments = [
        _segment(segment_id="S1", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
        _segment(segment_id="S2", image_path="b.png", duration_ms=1000, transition_in="CUT", transition_out="CROSSFADE"),
        _segment(segment_id="S3", image_path="c.png", duration_ms=2000, transition_in="CUT", transition_out="CUT"),
    ]
    request = _request(segments, settings=_settings(crossfade_duration_ms=250))
    command = _build(request)

    t_values = [command[i + 1] for i, arg in enumerate(command) if arg == "-t"]
    assert t_values[0] == "1.000"  # S1: plain CUT into S2, unextended
    assert t_values[1] == "1.250"  # S2: extended by the crossfade into S3
    assert t_values[2] == "2.000"  # S3: last segment, never extended

    filter_complex = command[command.index("-filter_complex") + 1]
    # S1->S2 is a plain concat (offset would be meaningless for a CUT join).
    assert "concat=n=2:v=1:a=0[vjoin1]" in filter_complex
    # S2->S3's xfade offset is the sum of ORIGINAL S1+S2 durations (2.000s),
    # regardless of the earlier CUT join.
    assert "xfade=transition=fade:duration=0.250:offset=2.000[vout]" in filter_complex

    last_t = command[len(command) - 1 - command[::-1].index("-t") + 1]
    assert last_t == "4.000"  # naive sum of all three original durations


def test_three_segments_crossfade_then_cut():
    segments = [
        _segment(segment_id="S1", duration_ms=1000, transition_in="CUT", transition_out="CROSSFADE"),
        _segment(segment_id="S2", image_path="b.png", duration_ms=1500, transition_in="CUT", transition_out="CUT"),
        _segment(segment_id="S3", image_path="c.png", duration_ms=2000, transition_in="CUT", transition_out="CUT"),
    ]
    request = _request(segments, settings=_settings(crossfade_duration_ms=300))
    command = _build(request)

    filter_complex = command[command.index("-filter_complex") + 1]
    assert "xfade=transition=fade:duration=0.300:offset=1.000[vjoin1]" in filter_complex
    assert "[vjoin1][vnorm2]concat=n=2:v=1:a=0[vout]" in filter_complex

    last_t = command[len(command) - 1 - command[::-1].index("-t") + 1]
    assert last_t == "4.500"  # naive sum: 1000+1500+2000ms


def test_default_crossfade_duration_is_300ms():
    segments = [
        _segment(segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
        _segment(segment_id="S2", image_path="b.png", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
    ]
    request = _request(segments)  # default settings, no crossfade_duration_ms override
    command = _build(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "duration=0.300" in filter_complex


def test_custom_crossfade_duration_reflected():
    segments = [
        _segment(segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
        _segment(segment_id="S2", image_path="b.png", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
    ]
    request = _request(segments, settings=_settings(crossfade_duration_ms=150))
    command = _build(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "duration=0.150" in filter_complex


def test_crossfade_narration_filter_chain_unchanged():
    """CROSSFADE is visual-only -- the audio filter chain must apad each
    segment's narration against its own ORIGINAL duration_ms, never the
    video-side-extended one."""
    segments = [
        _segment(segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
        _segment(segment_id="S2", image_path="b.png", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
    ]
    request = _request(segments, settings=_settings(crossfade_duration_ms=300))
    command = _build(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "apad=whole_dur=1.000" in filter_complex
    assert "concat=n=2:v=0:a=1[aout]" in filter_complex


def test_crossfade_determinism():
    segments = [
        _segment(segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
        _segment(segment_id="S2", image_path="b.png", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
    ]
    request1 = _request(segments, settings=_settings(crossfade_duration_ms=300))
    request2 = _request(
        [
            _segment(segment_id="S1", duration_ms=1000, transition_out="CROSSFADE"),
            _segment(segment_id="S2", image_path="b.png", duration_ms=1000, transition_in="CUT", transition_out="CUT"),
        ],
        settings=_settings(crossfade_duration_ms=300),
    )
    assert _build(request1) == _build(request2)


# ---------------------------------------------------------------------------
# Phase 29: motion-lite filter construction
# ---------------------------------------------------------------------------


def test_static_motion_uses_plain_scale_only():
    request = _request([_segment(motion="STATIC")])
    command = _build(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "[0:v]scale=1920:1080,fps=30,format=yuv420p,setsar=1,settb=AVTB[vnorm0]" in filter_complex


def test_slow_zoom_in_uses_zoompan_with_increasing_range():
    request = _request([_segment(motion="SLOW_ZOOM_IN", duration_ms=2000)])
    command = _build(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "zoompan=z='(1.0+(1.06-1.0)*on/" in filter_complex
    assert "s=1920x1080:fps=30" in filter_complex


def test_slow_zoom_out_uses_zoompan_with_decreasing_range():
    request = _request([_segment(motion="SLOW_ZOOM_OUT", duration_ms=2000)])
    command = _build(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "zoompan=z='(1.06+(1.0-1.06)*on/" in filter_complex


def test_pan_left_and_pan_right_use_distinct_crop_expressions():
    left_request = _request([_segment(motion="PAN_LEFT", duration_ms=2000)])
    right_request = _request([_segment(motion="PAN_RIGHT", duration_ms=2000)])
    left_filter = _build(left_request)[_build(left_request).index("-filter_complex") + 1]
    right_filter = _build(right_request)[_build(right_request).index("-filter_complex") + 1]

    assert "scale=2016:1080,crop=w=1920:h=1080:x='min(96,max(0,96*(1-t/2.000)))'" in left_filter
    assert "scale=2016:1080,crop=w=1920:h=1080:x='min(96,max(0,96*t/2.000))'" in right_filter
    assert left_filter != right_filter


def test_motion_never_changes_input_loop_duration():
    """Motion is a filter-graph concern only -- it must never alter a
    segment's own -t (loop duration), unlike CROSSFADE's deliberate
    extension."""
    request = _request([_segment(motion="SLOW_ZOOM_IN", duration_ms=1000)])
    command = _build(request)
    assert command[command.index("-t") + 1] == "1.000"


def test_motion_parameter_determinism():
    request1 = _request([_segment(motion="PAN_RIGHT", duration_ms=2000)])
    request2 = _request([_segment(motion="PAN_RIGHT", duration_ms=2000)])
    assert _build(request1) == _build(request2)


# ---------------------------------------------------------------------------
# Phase 30: narration only (no cues) -- exactly Phase 29 behavior
# ---------------------------------------------------------------------------


def test_no_audio_mix_plan_equals_empty_plan():
    request = _request([_segment()])
    assert _build(request) == _build(request, AudioMixPlan())


def test_empty_plan_produces_no_music_or_sfx_inputs():
    request = _request([_segment()])
    command = _build(request, AudioMixPlan())
    assert "-stream_loop" not in command
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "amix" not in filter_complex
    assert filter_complex.rstrip().endswith("[aout]")


# ---------------------------------------------------------------------------
# Phase 30: narration + music
# ---------------------------------------------------------------------------


def test_single_region_music_input_and_loop_flag():
    request = _request(
        [_segment(duration_ms=1000)],
        audio_bindings=AudioAssetBindings(music_bed_path="music.wav"),
    )
    plan = AudioMixPlan(music_regions=[_music_region(0, 1000)])
    command = _build(request, plan)

    assert "-stream_loop" in command
    assert command[command.index("-stream_loop") + 1] == "-1"
    assert "music.wav" in command
    # -stream_loop/-i for music comes AFTER every image/narration input.
    assert command.index("music.wav") > command.index("a.wav")


def test_single_region_music_filter_trim_and_gain():
    request = _request(
        [_segment(duration_ms=1500)],
        audio_bindings=AudioAssetBindings(music_bed_path="music.wav"),
    )
    plan = AudioMixPlan(music_regions=[_music_region(0, 1500, gain_db=-24.0)])
    command = _build(request, plan)
    filter_complex = command[command.index("-filter_complex") + 1]

    # No asplit/acrossfade needed for a single, never-crossfaded region --
    # the SAME atrim mechanism handles both "loop" (source shorter than
    # needed) and "trim" (source longer than needed): -stream_loop -1
    # always supplies enough material, and atrim's own end= always caps
    # it, regardless of the real source file's own length.
    assert "asplit" not in filter_complex
    assert "acrossfade" not in filter_complex
    assert "atrim=start=0.000:end=1.500" in filter_complex
    assert "volume=-24.0dB" in filter_complex
    assert "adelay=0:all=1[amusicdelayed]" in filter_complex
    assert "amix=inputs=2:duration=longest:normalize=0[aout]" in filter_complex
    assert "[anarr]" in filter_complex  # narration renamed once mixing is engaged


def test_music_region_delayed_to_its_own_bed_start():
    request = _request(
        [_segment(segment_id="S1", duration_ms=2000), _segment(segment_id="S2", image_path="b.png", duration_ms=2000)],
        audio_bindings=AudioAssetBindings(music_bed_path="music.wav"),
    )
    plan = AudioMixPlan(music_regions=[_music_region(2000, 4000)])
    command = _build(request, plan)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "atrim=start=0.000:end=2.000" in filter_complex  # bed-relative, not timeline-absolute
    assert "adelay=2000:all=1[amusicdelayed]" in filter_complex


def test_duck_lift_regions_use_asplit_and_acrossfade():
    request = _request(
        [_segment(duration_ms=3000)],
        settings=_settings(music_gain_ramp_ms=80),
        audio_bindings=AudioAssetBindings(music_bed_path="music.wav"),
    )
    plan = AudioMixPlan(
        music_regions=[
            _music_region(0, 1000, gain_db=-24.0, state=MusicState.BED),
            _music_region(1000, 2000, gain_db=-32.0, state=MusicState.DUCK),
            _music_region(2000, 3000, gain_db=-20.0, state=MusicState.LIFT),
        ]
    )
    command = _build(request, plan)
    filter_complex = command[command.index("-filter_complex") + 1]

    assert "asplit=3" in filter_complex
    # Region 0/1 (not last) extended by the 80ms ramp; region 2 (last) is not.
    assert "atrim=start=0.000:end=1.080" in filter_complex
    assert "atrim=start=1.000:end=2.080" in filter_complex
    assert "atrim=start=2.000:end=3.000" in filter_complex
    assert filter_complex.count("acrossfade=d=0.080:c1=tri:c2=tri") == 2
    assert "adelay=0:all=1[amusicdelayed]" in filter_complex


def test_custom_gain_ramp_reflected():
    request = _request(
        [_segment(duration_ms=2000)],
        settings=_settings(music_gain_ramp_ms=150),
        audio_bindings=AudioAssetBindings(music_bed_path="music.wav"),
    )
    plan = AudioMixPlan(
        music_regions=[_music_region(0, 1000), _music_region(1000, 2000, state=MusicState.DUCK)]
    )
    command = _build(request, plan)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "acrossfade=d=0.150" in filter_complex


# ---------------------------------------------------------------------------
# Phase 30: narration + SFX
# ---------------------------------------------------------------------------


def test_sfx_input_included_after_music():
    request = _request(
        [_segment(duration_ms=5000)],
        audio_bindings=AudioAssetBindings(
            music_bed_path="music.wav", sfx_by_id={"whoosh": "whoosh.wav"}
        ),
    )
    plan = AudioMixPlan(
        music_regions=[_music_region(0, 5000)],
        sfx_events=[_sfx_event(2000, "whoosh", "whoosh.wav")],
    )
    command = _build(request, plan)
    assert "whoosh.wav" in command
    assert command.index("whoosh.wav") > command.index("music.wav")


def test_sfx_exact_adelay_and_gain():
    request = _request(
        [_segment(duration_ms=5000)],
        audio_bindings=AudioAssetBindings(sfx_by_id={"whoosh": "whoosh.wav"}),
    )
    plan = AudioMixPlan(sfx_events=[_sfx_event(2000, "whoosh", "whoosh.wav")])
    command = _build(request, plan)
    filter_complex = command[command.index("-filter_complex") + 1]

    assert "adelay=2000:all=1[asfx0]" in filter_complex
    assert f"volume={_settings().sfx_gain_db}dB" in filter_complex
    assert "atrim=start=0.000:end=3.000" in filter_complex  # 5000ms - 2000ms remaining
    assert "amix=inputs=2:duration=longest:normalize=0[aout]" in filter_complex


def test_sfx_trim_at_timeline_end():
    request = _request(
        [_segment(duration_ms=5000)],
        audio_bindings=AudioAssetBindings(sfx_by_id={"whoosh": "whoosh.wav"}),
    )
    plan = AudioMixPlan(sfx_events=[_sfx_event(4950, "whoosh", "whoosh.wav")])
    command = _build(request, plan)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "atrim=start=0.000:end=0.050" in filter_complex  # only 50ms remains
    assert "adelay=4950:all=1" in filter_complex


def test_custom_sfx_gain_reflected():
    request = _request(
        [_segment(duration_ms=2000)],
        settings=_settings(sfx_gain_db=-6.0),
        audio_bindings=AudioAssetBindings(sfx_by_id={"whoosh": "whoosh.wav"}),
    )
    plan = AudioMixPlan(sfx_events=[_sfx_event(500, "whoosh", "whoosh.wav")])
    command = _build(request, plan)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "volume=-6.0dB" in filter_complex


# ---------------------------------------------------------------------------
# Phase 30: full mix -- narration + music + several SFX
# ---------------------------------------------------------------------------


def test_full_mix_input_and_filter_ordering():
    segments = [
        _segment(segment_id="S1", image_path="a.png", narration_clips=[VideoNarrationClip(file_path="a.wav")], duration_ms=3000),
        _segment(segment_id="S2", image_path="b.png", narration_clips=[VideoNarrationClip(file_path="b.wav")], duration_ms=3000),
    ]
    request = _request(
        segments,
        audio_bindings=AudioAssetBindings(
            music_bed_path="music.wav", sfx_by_id={"whoosh": "whoosh.wav", "ding": "ding.wav"}
        ),
    )
    plan = AudioMixPlan(
        music_regions=[_music_region(0, 6000)],
        sfx_events=[
            _sfx_event(1000, "whoosh", "whoosh.wav"),
            _sfx_event(4000, "ding", "ding.wav"),
        ],
    )
    command = _build(request, plan)

    # Deterministic input ordering: images, then narration, then music, then SFX.
    assert command.index("a.png") < command.index("b.png") < command.index("a.wav")
    assert command.index("b.wav") < command.index("music.wav") < command.index("whoosh.wav")
    assert command.index("whoosh.wav") < command.index("ding.wav")

    filter_complex = command[command.index("-filter_complex") + 1]
    # Deterministic filter ordering: video, narration, music, sfx0, sfx1, amix.
    assert filter_complex.index("[vout]") < filter_complex.index("[anarr]")
    assert filter_complex.index("[anarr]") < filter_complex.index("amusicdelayed")
    assert filter_complex.index("amusicdelayed") < filter_complex.index("[asfx0]")
    assert filter_complex.index("[asfx0]") < filter_complex.index("[asfx1]")
    assert filter_complex.rstrip().endswith(
        "[anarr][amusicdelayed][asfx0][asfx1]amix=inputs=4:duration=longest:normalize=0[aout]"
    )


def test_full_mix_paths_with_spaces_and_windows_paths():
    from pathlib import Path

    music_path = str(Path("my folder/music bed.wav"))
    sfx_path = str(Path("my folder/whoosh sfx.wav"))
    request = _request(
        [_segment(image_path=r"C:\videos\a.png", narration_clips=[VideoNarrationClip(file_path=r"C:\videos\a.wav")])],
        audio_bindings=AudioAssetBindings(music_bed_path=music_path, sfx_by_id={"whoosh": sfx_path}),
    )
    plan = AudioMixPlan(
        music_regions=[_music_region(0, 1000)], sfx_events=[_sfx_event(200, "whoosh", sfx_path)]
    )
    command = _build(request, plan)

    assert music_path in command
    assert sfx_path in command
    assert not any(
        " " in arg and arg not in (music_path, sfx_path)
        for arg in command if isinstance(arg, str)
    )


def test_full_mix_is_argument_list_not_shell_string():
    request = _request(
        [_segment()],
        audio_bindings=AudioAssetBindings(music_bed_path="music.wav", sfx_by_id={"whoosh": "whoosh.wav"}),
    )
    plan = AudioMixPlan(
        music_regions=[_music_region(0, 1000)], sfx_events=[_sfx_event(200, "whoosh", "whoosh.wav")]
    )
    command = _build(request, plan)
    assert isinstance(command, list)
    assert all(isinstance(arg, str) for arg in command)


def test_full_mix_determinism():
    def _make():
        request = _request(
            [_segment()],
            audio_bindings=AudioAssetBindings(music_bed_path="music.wav", sfx_by_id={"whoosh": "whoosh.wav"}),
        )
        plan = AudioMixPlan(
            music_regions=[_music_region(0, 1000)], sfx_events=[_sfx_event(200, "whoosh", "whoosh.wav")]
        )
        return _build(request, plan)

    assert _make() == _make()


# ---------------------------------------------------------------------------
# Phase 30: timing invariants
# ---------------------------------------------------------------------------


def test_final_duration_unaffected_by_music_or_sfx():
    segments = [_segment(segment_id="S1", duration_ms=1000), _segment(segment_id="S2", image_path="b.png", duration_ms=1500)]
    request = _request(
        segments,
        audio_bindings=AudioAssetBindings(music_bed_path="music.wav", sfx_by_id={"whoosh": "whoosh.wav"}),
    )
    plan = AudioMixPlan(
        music_regions=[_music_region(0, 2500)], sfx_events=[_sfx_event(2000, "whoosh", "whoosh.wav")]
    )
    command = _build(request, plan)
    t_indices = [i for i, arg in enumerate(command) if arg == "-t"]
    last_t_value = command[t_indices[-1] + 1]
    assert last_t_value == "2.500"  # unchanged from the equivalent no-audio-mix case

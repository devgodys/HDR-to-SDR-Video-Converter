from hdr2sdr.runtime import capabilities
from hdr2sdr.core.commands import ConversionSettings


def test_detects_hardware_encoders():
    assert capabilities.is_hardware_encoder("h264_nvenc")
    assert capabilities.is_hardware_encoder("av1_amf")
    assert capabilities.is_hardware_encoder("av1_qsv")
    assert not capabilities.is_hardware_encoder("libx264")


def test_probe_results_are_cached_per_binary_and_request(monkeypatch, tmp_path):
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"ffmpeg")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    capabilities.clear_probe_cache()
    monkeypatch.setattr(capabilities.subprocess, "run", fake_run)
    try:
        assert capabilities.probe_encoder(binary, "h264_nvenc") == (True, "")
        assert capabilities.probe_encoder(binary, "h264_nvenc") == (True, "")
        assert len(calls) == 1
        assert "h264_nvenc" in calls[0][0]

        assert capabilities.probe_encoder(binary, "av1_qsv") == (True, "")
        assert len(calls) == 2
    finally:
        capabilities.clear_probe_cache()


def test_probe_failure_is_cached_with_diagnostic(monkeypatch, tmp_path):
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"ffmpeg")
    calls = []

    class Result:
        returncode = 1
        stderr = "device initialization failed"

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result()

    capabilities.clear_probe_cache()
    monkeypatch.setattr(capabilities.subprocess, "run", fake_run)
    try:
        expected = (False, "device initialization failed")
        assert capabilities.probe_libplacebo(binary) == expected
        assert capabilities.probe_libplacebo(binary) == expected
        assert len(calls) == 1
        assert "libplacebo" in calls[0][calls[0].index("-vf") + 1]
    finally:
        capabilities.clear_probe_cache()


def test_start_capability_check_returns_first_issue_without_probing_cpu_path():
    settings = ConversionSettings()
    issue = capabilities.check_start_capabilities(
        settings, ffmpeg="ffmpeg", tonemap_engine="cpu", tonemap_code="hable",
        has_libplacebo=False, has_st2094=False, encoders={"libx265"},
        libplacebo_probe=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()),
        encoder_probe=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))
    assert issue is None


def test_start_capability_check_reports_missing_gpu_support_first():
    settings = ConversionSettings()
    issue = capabilities.check_start_capabilities(
        settings, ffmpeg="ffmpeg", tonemap_engine="gpu", tonemap_code="bt.2390",
        has_libplacebo=False, has_st2094=False, encoders=set())
    assert issue.title == "GPU tonemap unavailable"


def test_start_capability_check_reports_encoder_probe_failure():
    settings = ConversionSettings(encoder_label="NVIDIA NVENC · H.264")
    issue = capabilities.check_start_capabilities(
        settings, ffmpeg="ffmpeg", tonemap_engine="cpu", tonemap_code="hable",
        has_libplacebo=False, has_st2094=False, encoders={"h264_nvenc"},
        encoder_probe=lambda *_a, **_k: (False, "driver error"))
    assert issue.title == "Hardware encoder unavailable"
    assert "driver error" in issue.message



def test_probe_encoder_uses_at_least_256x256_frame(monkeypatch, tmp_path):
    """FIX_NVENC_PREFLIGHT_2026-10-23: NVENC/HEVC rejects 128x128 frames
    ('Frame dimensions are less than the minimum supported value').
    Ensure probe_encoder uses 256x256 while probe_libplacebo stays 128x128."""
    import re
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"ffmpeg")
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(list(command))
        return Result()

    capabilities.clear_probe_cache()
    monkeypatch.setattr(capabilities.subprocess, "run", fake_run)
    try:
        assert capabilities.probe_encoder(binary, "hevc_nvenc") == (True, "")
        assert len(calls) == 1
        cmd = calls[0]
        lavfi_arg = cmd[cmd.index("-i") + 1]
        assert lavfi_arg == "color=c=black:s=256x256:r=1:d=1"
        m = re.search(r"s=(\d+)x(\d+)", lavfi_arg)
        assert m is not None
        w, h = int(m.group(1)), int(m.group(2))
        assert w >= 256 and h >= 256

        assert capabilities.probe_libplacebo(binary) == (True, "")
        assert len(calls) == 2
        lp_arg = calls[1][calls[1].index("-i") + 1]
        assert "s=128x128" in lp_arg
    finally:
        capabilities.clear_probe_cache()


def test_nvenc_preflight_256x256_all_hw_encoders_and_no_silent_cpu_fallback(monkeypatch, tmp_path):
    """FIX_NVENC_PREFLIGHT_2026-10-23:
    1) Every hardware encoder preflight probe via check_start_capabilities uses a >=256x256 synthetic frame.
    2) If a hardware encoder probe fails, check_start_capabilities reports 'Hardware encoder unavailable'
       and never mutates settings.encoder_label or silently falls back to CPU."""
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"ffmpeg")
    probed_cmds = []

    class ResultOk:
        returncode = 0
        stderr = ""

    def fake_run(command, **kwargs):
        probed_cmds.append(list(command))
        return ResultOk()

    capabilities.clear_probe_cache()
    monkeypatch.setattr(capabilities.subprocess, "run", fake_run)
    try:
        hw_cases = [
            ("NVIDIA NVENC \u00b7 H.265", "hevc_nvenc"),
            ("NVIDIA NVENC \u00b7 H.264", "h264_nvenc"),
            ("NVIDIA NVENC \u00b7 AV1", "av1_nvenc"),
            ("AMD AMF \u00b7 H.265", "hevc_amf"),
            ("Intel QSV \u00b7 H.265", "hevc_qsv"),
        ]
        all_encoders = {enc_id for _, enc_id in hw_cases} | {"libx265", "libx264"}
        for label, enc_id in hw_cases:
            probed_cmds.clear()
            capabilities.clear_probe_cache()
            s = ConversionSettings(encoder_label=label, bit_depth_label="8-bit (SDR standard)")
            issue = capabilities.check_start_capabilities(
                s,
                ffmpeg=str(binary),
                tonemap_engine="cpu",
                tonemap_code="hable",
                has_libplacebo=True,
                has_st2094=True,
                encoders=all_encoders,
            )
            assert issue is None
            assert s.encoder_label == label
            assert len(probed_cmds) == 1
            cmd = probed_cmds[0]
            assert cmd[cmd.index("-c:v") + 1] == enc_id
            assert cmd[cmd.index("-i") + 1] == "color=c=black:s=256x256:r=1:d=1"

        # Verify no silent fallback to CPU when probe fails
        s_fail = ConversionSettings(encoder_label="NVIDIA NVENC \u00b7 H.265")
        issue_fail = capabilities.check_start_capabilities(
            s_fail,
            ffmpeg=str(binary),
            tonemap_engine="cpu",
            tonemap_code="hable",
            has_libplacebo=True,
            has_st2094=True,
            encoders=all_encoders,
            encoder_probe=lambda *_a, **_k: (
                False,
                "InitializeEncoder failed: invalid param (8): Frame dimensions are less than the minimum supported value.",
            ),
        )
        assert issue_fail is not None
        assert issue_fail.title == "Hardware encoder unavailable"
        assert "InitializeEncoder failed" in issue_fail.message
        assert s_fail.encoder_label == "NVIDIA NVENC \u00b7 H.265"
    finally:
        capabilities.clear_probe_cache()


def test_strip_libplacebo_backtrace_and_transient_oom_cache_invalidation(monkeypatch, tmp_path):
    """Regression for libplacebo VK_ERROR_OUT_OF_DEVICE_MEMORY preflight failure:
    1) Raw C stack traces ('Backtrace:', '#0 0x7ff7...') are stripped from stderr while
       keeping Vulkan heap/pool and plane texture diagnostic lines.
    2) invalidate_transient_libplacebo_cache() drops cached libplacebo OOM failures so a
       retry after stopping a background 4K preview process can re-probe cleanly."""
    raw_stderr = """[libplacebo @ 0000025d8a8f4d00] Allocation of size  256K failed: VK_ERROR_OUT_OF_DEVICE_MEMORY!
[libplacebo @ 0000025d8a8f4d00]   for malloc: ../src/utils/upload.c:250
[libplacebo @ 0000025d8a8f4d00] Backtrace:
[libplacebo @ 0000025d8a8f4d00]     #0  0x7ff7b25f8961 in pl_get_detected_fields+0x9cc41 (D:\\HDR2SDR\\bin\\ffmpeg.exe+0x23c8961)
[libplacebo @ 0000025d8a8f4d00]     #1  0x7ff7b25df5ad in pl_get_detected_fields+0x8388d (D:\\HDR2SDR\\bin\\ffmpeg.exe+0x23af5ad)
[libplacebo @ 0000025d8a8f4d00] No slab to serve request for   32K bytes (with alignment 0x400) in pool 0!
[libplacebo @ 0000025d8a8f4d00] Failed initializing plane texture!
[libplacebo @ 0000025d8a8f4d00] Failed mapping frame id 0 with PTS 0.000000
[out#0/null @ 0000025d8a887200] Nothing was written into output file, because at least one of its streams received no packets."""

    cleaned = capabilities._strip_libplacebo_backtrace(raw_stderr)
    assert "VK_ERROR_OUT_OF_DEVICE_MEMORY!" in cleaned
    assert "Failed initializing plane texture!" in cleaned
    assert "Nothing was written into output file" in cleaned
    assert "Backtrace:" not in cleaned
    assert "0x7ff7b25f8961" not in cleaned

    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"ffmpeg")
    outcomes = [
        (1, raw_stderr),
        (1, raw_stderr),
        (0, ""),
    ]
    calls = []

    class FakeResult:
        def __init__(self, rc, err):
            self.returncode = rc
            self.stderr = err

    def fake_run(command, **kwargs):
        calls.append(list(command))
        rc, err = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        return FakeResult(rc, err)

    capabilities.clear_probe_cache()
    monkeypatch.setattr(capabilities.subprocess, "run", fake_run)
    try:
        ok1, detail1 = capabilities.probe_libplacebo(binary)
        assert not ok1
        assert "VK_ERROR_OUT_OF_DEVICE_MEMORY!" in detail1
        assert "Backtrace:" not in detail1
        # Initial call automatically retried once on VK_ERROR_OUT_OF_DEVICE_MEMORY
        assert len(calls) == 2

        # Cached while still in _CACHE
        assert capabilities.probe_libplacebo(binary) == (False, detail1)
        assert len(calls) == 2

        # After stopping preview process, transient OOM cache is invalidated and re-probed
        capabilities.invalidate_transient_libplacebo_cache()
        assert capabilities.probe_libplacebo(binary) == (True, "")
        assert len(calls) == 3
    finally:
        capabilities.clear_probe_cache()


def test_error_hint_scanner_detects_libplacebo_plane_texture_and_slab_oom():
    """ErrorHintScanner latches gpu_tonemap on libplacebo upload/plane texture OOM lines."""
    from hdr2sdr.core.progress import ErrorHintScanner

    for sample in (
        "[libplacebo @ 0000025d8a8f4d00] Allocation of size  256K failed: VK_ERROR_OUT_OF_DEVICE_MEMORY!",
        "[libplacebo @ 0000025d8a8f4d00] No slab to serve request for   32K bytes (with alignment 0x400) in pool 0!",
        "[libplacebo @ 0000025d8a8f4d00] Failed initializing plane texture!",
        "[libplacebo @ 0000025d8a8f4d00] Failed mapping frame id 0 with PTS 0.000000",
    ):
        scanner = ErrorHintScanner()
        assert not scanner.gpu_tonemap
        scanner.feed(sample)
        assert scanner.gpu_tonemap, f"Failed to detect gpu_tonemap on: {sample}"


def test_probe_encoder_fails_on_128x128_but_succeeds_with_actual_256x256_probe(monkeypatch, tmp_path):
    """Case 1 (NVENC 256x256 minimum frame size vs libplacebo 128x128):
    Simulate an FFmpeg + NVENC driver that rejects any encoder frame < 256x256 with
    'InitializeEncoder failed: invalid param (8): Frame dimensions are less than the minimum supported value.'
    while allowing libplacebo (-vf libplacebo=..., -f null -) at 128x128.
    Verify check_start_capabilities passes both probes in a single GPU + NVENC preflight."""
    import re
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"ffmpeg")
    seen_probes = []

    class FakeResult:
        def __init__(self, rc, err=""):
            self.returncode = rc
            self.stderr = err

    def fake_run(command, **kwargs):
        cmd = list(command)
        lavfi = cmd[cmd.index("-i") + 1]
        m = re.search(r"s=(\d+)x(\d+)", lavfi)
        w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        if "-c:v" in cmd:
            enc = cmd[cmd.index("-c:v") + 1]
            seen_probes.append(("encoder", enc, w, h))
            if w < 256 or h < 256:
                return FakeResult(
                    1,
                    "[hevc_nvenc] InitializeEncoder failed: invalid param (8): "
                    "Frame dimensions are less than the minimum supported value.",
                )
            return FakeResult(0, "")
        seen_probes.append(("libplacebo", "bt.2390", w, h))
        return FakeResult(0, "")

    capabilities.clear_probe_cache()
    monkeypatch.setattr(capabilities.subprocess, "run", fake_run)
    try:
        s = ConversionSettings(
            tonemap_method="BT.2390 \u00b7 GPU tonemap",
            encoder_label="NVIDIA NVENC \u00b7 H.265",
            bit_depth_label="8-bit (SDR standard)",
        )
        issue = capabilities.check_start_capabilities(
            s,
            ffmpeg=str(binary),
            tonemap_engine="gpu",
            tonemap_code="bt.2390",
            has_libplacebo=True,
            has_st2094=True,
            encoders={"hevc_nvenc", "libx265"},
        )
        assert issue is None
        assert seen_probes == [
            ("libplacebo", "bt.2390", 128, 128),
            ("encoder", "hevc_nvenc", 256, 256),
        ]
    finally:
        capabilities.clear_probe_cache()


def test_probe_libplacebo_transient_oom_succeeds_on_immediate_retry_and_keeps_gpu_tonemap(monkeypatch, tmp_path):
    """Case 2a (Transient Vulkan BAR1 OOM resolved on retry):
    When probe_libplacebo fails on attempt 1 with VK_ERROR_OUT_OF_DEVICE_MEMORY (e.g. while
    Windows WDDM is reclaiming BAR1 memory from a stopped 4K preview process) and succeeds on
    attempt 2, check_start_capabilities returns None and keeps GPU tonemapping active."""
    binary = tmp_path / "ffmpeg.exe"
    binary.write_bytes(b"ffmpeg")
    libplacebo_attempts = []

    class FakeResult:
        def __init__(self, rc, err=""):
            self.returncode = rc
            self.stderr = err

    def fake_run(command, **kwargs):
        cmd = list(command)
        if "-vf" in cmd and "libplacebo" in cmd[cmd.index("-vf") + 1]:
            libplacebo_attempts.append(cmd)
            if len(libplacebo_attempts) == 1:
                return FakeResult(
                    1,
                    "[libplacebo @ 0000025d8a8f4d00] Allocation of size  256K failed: VK_ERROR_OUT_OF_DEVICE_MEMORY!\n"
                    "[libplacebo @ 0000025d8a8f4d00] Failed initializing plane texture!",
                )
            return FakeResult(0, "")
        return FakeResult(0, "")

    capabilities.clear_probe_cache()
    monkeypatch.setattr(capabilities.subprocess, "run", fake_run)
    try:
        s = ConversionSettings(
            tonemap_method="BT.2390 \u00b7 GPU tonemap",
            encoder_label="NVIDIA NVENC \u00b7 H.265",
        )
        issue = capabilities.check_start_capabilities(
            s,
            ffmpeg=str(binary),
            tonemap_engine="gpu",
            tonemap_code="bt.2390",
            has_libplacebo=True,
            has_st2094=True,
            encoders={"hevc_nvenc", "libx265"},
        )
        assert issue is None
        assert len(libplacebo_attempts) == 2
        assert s.tonemap_method == "BT.2390 \u00b7 GPU tonemap"
    finally:
        capabilities.clear_probe_cache()

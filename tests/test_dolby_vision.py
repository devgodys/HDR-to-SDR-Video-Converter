from hdr2sdr.core.dolby_vision import dovi_configuration, dovi_profile, profile5_rpu_supported


def test_reads_profile_from_ffprobe_dovi_side_data():
    stream = {"side_data_list": [
        {"side_data_type": "Mastering display metadata"},
        {"side_data_type": "DOVI configuration record", "dv_profile": 5,
         "dv_bl_signal_compatibility_id": 0, "rpu_present_flag": 1},
    ]}
    assert dovi_configuration(stream)["dv_bl_signal_compatibility_id"] == 0
    assert dovi_profile(stream) == 5
    assert dovi_profile({"side_data_list": []}) is None
    assert dovi_profile({"side_data_list": [
        {"side_data_type": "DOVI configuration record", "dv_profile": "unknown"}
    ]}) is None


def test_profile5_requires_ffmpeg_gpu_libplacebo_but_other_profiles_do_not():
    assert profile5_rpu_supported(5, False, "gpu", True)
    assert not profile5_rpu_supported(5, True, "gpu", True)
    assert not profile5_rpu_supported(5, False, "cpu", True)
    assert not profile5_rpu_supported(5, False, "gpu", False)
    assert profile5_rpu_supported(8, True, "cpu", False)

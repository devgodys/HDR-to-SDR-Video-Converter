import pytest

from hdr2sdr.core.destination import DestinationPlan


def test_destination_plan_names_related_files(tmp_path):
    plan = DestinationPlan(tmp_path / "movie_SDR.mp4")

    assert plan.final == tmp_path / "movie_SDR.mp4"
    assert plan.part == tmp_path / "movie_SDR.part.mp4"
    assert plan.sidecar == tmp_path / "movie_SDR.part.mp4.json"
    assert plan.concat_list == tmp_path / "movie_SDR.resume-concat.txt"
    assert plan.resume_part(1) == tmp_path / "movie_SDR.resume-part1.mp4"
    assert DestinationPlan.concat_list_for(plan.part) == tmp_path / "movie_SDR.part.resume-concat.txt"


def test_destination_plan_rejects_nonpositive_resume_part(tmp_path):
    with pytest.raises(ValueError):
        DestinationPlan(tmp_path / "movie.mkv").resume_part(0)


@pytest.mark.parametrize(
    "source_name",
    [
        "movie_SDR.mp4",
        "movie_SDR.part.mp4",
        "movie_SDR.part.mp4.json",
        "movie_SDR.resume-concat.txt",
        "movie_SDR.part.resume-concat.txt",
        "movie_SDR.resume-part2.mp4",
    ],
)
def test_destination_plan_detects_all_output_collisions(tmp_path, source_name):
    plan = DestinationPlan(tmp_path / "movie_SDR.mp4")
    assert plan.collides_with(tmp_path / source_name)


def test_destination_plan_ignores_unrelated_resume_files(tmp_path):
    plan = DestinationPlan(tmp_path / "movie_SDR.mp4")
    assert not plan.collides_with(tmp_path / "other.resume-part2.mp4")
    assert not plan.collides_with(tmp_path / "nested" / "movie_SDR.resume-part2.mp4")

"""Performance-sensitive query and download helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event

from src.db.models import Profile, Video, init_db, reset_engine_cache
from src.direct_download import existing_local_video_path
from src.services import get_profile_stats, get_profiles_stats, video_to_dict


class DbTestCase(unittest.TestCase):
    def setUp(self):
        reset_engine_cache()
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.session = init_db(self.db_path)

    def tearDown(self):
        self.session.close()
        reset_engine_cache()
        self._tmp.cleanup()

    def _profile(self, username: str) -> Profile:
        profile = Profile(
            platform="tiktok",
            username=username,
            url=f"https://www.tiktok.com/@{username}",
        )
        self.session.add(profile)
        self.session.flush()
        return profile

    def _video(self, profile: Profile, vid: str, **kwargs) -> Video:
        row = Video(
            profile_id=profile.id,
            platform_video_id=vid,
            url=f"https://www.tiktok.com/@{profile.username}/video/{vid}",
            **kwargs,
        )
        self.session.add(row)
        return row


class EngineCacheTests(DbTestCase):
    def test_init_db_reuses_engine(self):
        first = self.session.get_bind()
        self.session.close()
        second_session = init_db(self.db_path)
        try:
            self.assertIs(second_session.get_bind(), first)
        finally:
            second_session.close()

    def test_init_db_runs_migrations_once(self):
        calls = {"n": 0}
        import src.db.models as models

        original = models.run_migrations

        def wrapped(db_path):
            calls["n"] += 1
            return original(db_path)

        with patch.object(models, "run_migrations", side_effect=wrapped):
            reset_engine_cache()
            s1 = init_db(self.db_path)
            s1.close()
            s2 = init_db(self.db_path)
            s2.close()
        self.assertEqual(calls["n"], 1)

    def test_sqlite_enables_wal(self):
        mode = self.session.execute(__import__("sqlalchemy").text("PRAGMA journal_mode")).scalar()
        self.assertEqual(str(mode).lower(), "wal")


class ProfileStatsTests(DbTestCase):
    def test_batch_stats_match_per_profile_and_use_one_aggregate_query(self):
        a = self._profile("alpha")
        b = self._profile("beta")
        empty = self._profile("empty")
        self._video(a, "1", gmv=10.0, commission=1.0, is_downloaded=True)
        self._video(a, "2", gmv=5.0, commission=0.5, is_downloaded=False)
        self._video(a, "3", gmv=0.0, commission=0.0, is_downloaded=True)
        self._video(b, "4", gmv=100.0, commission=9.0, is_downloaded=False)
        self.session.commit()

        sql = []

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            sql.append(statement)

        engine = self.session.get_bind()
        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            stats = get_profiles_stats(self.session, [a, b, empty])
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)

        self.assertEqual(stats[a.id]["total"], 3)
        self.assertEqual(stats[a.id]["downloaded"], 2)
        self.assertEqual(stats[a.id]["pending"], 1)
        self.assertEqual(stats[a.id]["total_gmv"], 15.0)
        self.assertEqual(stats[a.id]["total_commission"], 1.5)
        self.assertEqual(stats[a.id]["with_gmv"], 2)
        self.assertEqual(stats[b.id]["total"], 1)
        self.assertEqual(stats[b.id]["total_gmv"], 100.0)
        self.assertEqual(stats[empty.id]["total"], 0)
        self.assertEqual(stats[empty.id]["downloaded"], 0)

        aggregate_sql = [s for s in sql if "from videos" in s.lower()]
        self.assertEqual(len(aggregate_sql), 1, sql)
        self.assertIn("group by", aggregate_sql[0].lower())
        self.assertIn("count(", aggregate_sql[0].lower())

    def test_single_profile_stats_uses_aggregate_not_row_load(self):
        profile = self._profile("solo")
        self._video(profile, "1", gmv=3.0, is_downloaded=True)
        self._video(profile, "2", gmv=7.0, is_downloaded=False)
        self.session.commit()

        sql = []

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            sql.append(statement)

        engine = self.session.get_bind()
        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            stats = get_profile_stats(self.session, profile.id)
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)

        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["downloaded"], 1)
        self.assertEqual(stats["total_gmv"], 10.0)
        video_selects = [s for s in sql if "from videos" in s.lower()]
        self.assertTrue(video_selects)
        self.assertIn("count(", video_selects[0].lower())


class VideoDictTests(DbTestCase):
    def test_video_to_dict_does_not_stat_filesystem(self):
        profile = self._profile("statless")
        video = self._video(
            profile,
            "9",
            title="x",
            file_path="/definitely/missing/on/disk.mp4",
            is_downloaded=True,
        )
        self.session.commit()

        with patch("src.services.Path.exists", side_effect=AssertionError("exists")):
            with patch("src.services.Path.stat", side_effect=AssertionError("stat")):
                data = video_to_dict(video)

        self.assertTrue(data["has_server_file"])
        self.assertEqual(data["download_location"], "server")


class LocalDownloadTests(unittest.TestCase):
    def test_existing_local_video_path_requires_nonempty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Video(
                profile_id=1,
                platform_video_id="1",
                url="https://example.com/1",
                file_path=str(Path(tmp) / "nope.mp4"),
            )
            self.assertIsNone(existing_local_video_path(missing))

            empty = Path(tmp) / "empty.mp4"
            empty.write_bytes(b"")
            blank = Video(
                profile_id=1,
                platform_video_id="2",
                url="https://example.com/2",
                file_path=str(empty),
            )
            self.assertIsNone(existing_local_video_path(blank))

            real = Path(tmp) / "ok.mp4"
            real.write_bytes(b"abcd")
            present = Video(
                profile_id=1,
                platform_video_id="3",
                url="https://example.com/3",
                file_path=str(real),
            )
            self.assertEqual(existing_local_video_path(present), real)

            none = Video(
                profile_id=1,
                platform_video_id="4",
                url="https://example.com/4",
            )
            self.assertIsNone(existing_local_video_path(none))


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest

from multihub.source_paths import repository_sources_dir


class SourceDirectoryTests(unittest.TestCase):
    def test_shared_source_directory_is_repository_root_sources(self) -> None:
        expected = Path(__file__).resolve().parents[2] / "sources"
        self.assertEqual(repository_sources_dir(), expected)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.search.providers.github_reader import GitHubReaderProvider


class GitHubReaderProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = GitHubReaderProvider()

    def test_parse_target_supports_repo_tree_and_blob_urls(self) -> None:
        repo_target = self.provider._parse_target("https://github.com/openai/openai-python")
        tree_target = self.provider._parse_target("https://github.com/openai/openai-python/tree/main/src/openai")
        blob_target = self.provider._parse_target("https://github.com/openai/openai-python/blob/main/src/openai/__init__.py")

        self.assertIsNotNone(repo_target)
        self.assertEqual(repo_target.kind, "repo")

        self.assertIsNotNone(tree_target)
        self.assertEqual(tree_target.kind, "tree")
        self.assertEqual(tree_target.tail_segments[:2], ("main", "src"))

        self.assertIsNotNone(blob_target)
        self.assertEqual(blob_target.kind, "blob")
        self.assertEqual(blob_target.tail_segments[-1], "__init__.py")

    def test_resolve_ref_path_prefers_longest_matching_branch_name(self) -> None:
        ref, path = self.provider._resolve_ref_path_from_candidates(
            tail_segments=("feature", "beta", "src", "main.py"),
            ref_candidates=("feature/beta", "feature", "main"),
            default_branch="main",
        )

        self.assertEqual(ref, "feature/beta")
        self.assertEqual(path, "src/main.py")

    def test_score_file_item_prefers_source_code_in_source_directories(self) -> None:
        docker_score = self.provider._score_file_item(
            {"path": ".devcontainer/Dockerfile", "size": 320}
        )
        source_score = self.provider._score_file_item(
            {"path": "src/openai/_client.py", "size": 51842}
        )

        self.assertGreater(source_score, docker_score)


if __name__ == "__main__":
    unittest.main()

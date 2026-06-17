from migration_task_pipeline.layers.a_seed_collection.github_urls import extract_github_urls, normalize_github_url


def test_normalize_common_github_variants():
    cases = {
        "https://github.com/ACEsuit/mace.git": "https://github.com/ACEsuit/mace",
        "https://github.com/Lightning-AI/pytorch-lightning/tree/main?tab=readme": "https://github.com/Lightning-AI/pytorch-lightning",
        "github.com/openai/triton/issues/123#issuecomment": "https://github.com/openai/triton",
        "git@github.com:owner/repo.git": "https://github.com/owner/repo",
    }
    for value, expected in cases.items():
        normalized = normalize_github_url(value)
        assert normalized is not None
        assert normalized.repo_url == expected


def test_extract_github_urls_from_text_dedupes_and_ignores_org_only_links():
    text = """
    Source: https://github.com/Owner/Repo/blob/main/a.py
    Mirror: github.com/owner/repo/issues
    Org: https://github.com/features
    Other: https://github.com/Another/Project.
    """

    urls = extract_github_urls(text)

    assert [url.repo_url for url in urls] == [
        "https://github.com/Owner/Repo",
        "https://github.com/Another/Project",
    ]


def test_normalize_rejects_non_repo_urls():
    assert normalize_github_url("https://github.com/owner") is None
    assert normalize_github_url("https://example.com/owner/repo") is None

from migration_task_pipeline.dedupe import dedupe_seed_records


def test_dedupe_merges_sources_and_prefers_stronger_provenance():
    records = [
        {
            "source": "pypi",
            "package_name": "demo-pypi",
            "repo_url": "https://github.com/Owner/Repo",
            "repo_key": "owner/repo",
            "url_extract_field": "description",
            "matched_keywords": ["cuda"],
            "license": "MIT",
            "homepage": "https://pypi.example",
            "downloads_30d": 2000,
            "collected_at": "2026-06-16T00:00:00+00:00",
        },
        {
            "source": "conda-forge",
            "package_name": "demo-conda",
            "repo_url": "https://github.com/Owner/Repo",
            "repo_key": "owner/repo",
            "url_extract_field": "dev_url",
            "matched_keywords": ["torch", "cuda"],
            "license": "Apache-2.0",
            "homepage": "https://conda.example",
            "downloads_30d": "",
            "collected_at": "2026-06-16T00:00:01+00:00",
        },
    ]

    rows = dedupe_seed_records(records)

    assert len(rows) == 1
    row = rows[0]
    assert row["package_name"] == "demo-conda"
    assert row["sources"] == ["conda-forge", "pypi"]
    assert row["package_names"] == ["demo-conda", "demo-pypi"]
    assert row["matched_keywords"] == ["cuda", "torch"]
    assert row["downloads_30d"] == 2000
    assert row["source_count"] == 2


def test_dedupe_preserves_existing_github_metadata_from_any_source():
    records = [
        {
            "source": "pypi",
            "package_name": "demo",
            "repo_url": "https://github.com/Owner/Repo",
            "repo_key": "owner/repo",
            "url_extract_field": "project_urls.Source",
            "matched_keywords": ["cuda"],
            "downloads_30d": 1200,
            "collected_at": "2026-06-16T00:00:00+00:00",
        },
        {
            "source": "github-search",
            "package_name": "",
            "repo_url": "https://github.com/Owner/Repo",
            "repo_key": "owner/repo",
            "url_extract_field": "html_url",
            "matched_keywords": ["cuda"],
            "downloads_30d": "",
            "collected_at": "2026-06-16T00:00:01+00:00",
            "github_stars": 25,
            "github_archived": False,
            "github_license": "MIT",
            "github_size_kb": 100,
            "github_pushed_at": "2026-06-01T00:00:00Z",
        },
    ]

    rows = dedupe_seed_records(records)

    assert len(rows) == 1
    assert rows[0]["package_name"] == "demo"
    assert rows[0]["github_stars"] == 25
    assert rows[0]["github_license"] == "MIT"

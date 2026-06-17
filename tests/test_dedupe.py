from migration_task_pipeline.layers.a_seed_collection.dedupe import dedupe_seed_records


def test_dedupe_merges_sources_and_prefers_stronger_provenance():
    records = [
        {
            "source": "github-search",
            "package_name": "",
            "repo_url": "https://github.com/Owner/Repo",
            "repo_key": "owner/repo",
            "url_extract_field": "html_url",
            "matched_keywords": ["cuda"],
            "license": "MIT",
            "homepage": "https://github.com/Owner/Repo",
            "downloads_30d": "",
            "collected_at": "2026-06-16T00:00:00+00:00",
        },
        {
            "source": "github-search",
            "package_name": "",
            "repo_url": "https://github.com/Owner/Repo",
            "repo_key": "owner/repo",
            "url_extract_field": "html_url",
            "matched_keywords": ["torch", "cuda"],
            "license": "Apache-2.0",
            "homepage": "https://repo.example",
            "downloads_30d": "",
            "collected_at": "2026-06-16T00:00:01+00:00",
        },
    ]

    rows = dedupe_seed_records(records)

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "github-search"
    assert row["sources"] == ["github-search"]
    assert row["package_names"] == []
    assert row["matched_keywords"] == ["cuda", "torch"]
    assert row["downloads_30d"] == ""
    assert row["source_count"] == 1


def test_dedupe_preserves_existing_github_metadata_from_any_source():
    records = [
        {
            "source": "github-search",
            "package_name": "",
            "repo_url": "https://github.com/Owner/Repo",
            "repo_key": "owner/repo",
            "url_extract_field": "html_url",
            "matched_keywords": ["cuda"],
            "downloads_30d": "",
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
    assert rows[0]["source"] == "github-search"
    assert rows[0]["github_stars"] == 25
    assert rows[0]["github_license"] == "MIT"

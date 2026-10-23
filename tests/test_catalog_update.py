"""Catalog detection and snapshot refresh. HTTP is a fake fetch."""

import json
from datetime import date
from pathlib import Path

import yaml

from src.catalog_update import (
    CatalogCheckResult,
    FetchResult,
    check_for_newer_catalog,
    course_listing_entries,
    extract_course_snapshot,
    extract_course_listing_snapshot,
    extract_program_snapshot,
    is_newer_catalog,
    parse_published_catalogs,
    pull_request_body,
    select_current_catalog,
)

ROOT = Path(__file__).resolve().parents[1]


MENU_2026 = """
<select name="catalog">
  <option value="23" selected>2026-2027 Academic Catalog</option>
  <option value="10">MyRoadmaps Catalog</option>
  <option value="17">2025-2026 Academic Catalog [ARCHIVED CATALOG]</option>
  <option value="15">2024-2025 Academic Catalog [ARCHIVED CATALOG]</option>
</select>
<select name="search_database">
  <option value="3" selected>Courses</option>
</select>
"""

MENU_2027 = """
<select name="catalog">
  <option value="24" selected>2027-2028 Academic Catalog</option>
  <option value="23">2026-2027 Academic Catalog [ARCHIVED CATALOG]</option>
  <option value="17">2025-2026 Academic Catalog [ARCHIVED CATALOG]</option>
</select>
"""


def test_catalog_menu_selects_the_current_academic_year():
    editions = parse_published_catalogs(MENU_2026)
    assert [(item.catoid, item.year, item.selected) for item in editions] == [
        ("23", "2026-2027", True),
        ("17", "2025-2026", False),
        ("15", "2024-2025", False),
    ]
    current = select_current_catalog(editions)
    assert current is not None
    assert current.catoid == "23"
    assert current.year == "2026-2027"
    assert is_newer_catalog("2027-2028", "2026-2027") is True
    assert is_newer_catalog("2026-2027", "2026-2027") is False
    assert is_newer_catalog("2025-2026", "2026-2027") is False


def test_menu_without_one_current_year_is_unusable():
    assert (
        select_current_catalog(parse_published_catalogs("<html>hello</html>")) is None
    )
    both = """
    <select name="catalog">
      <option value="24" selected>2027-2028 Academic Catalog</option>
      <option value="23" selected>2026-2027 Academic Catalog</option>
    </select>
    """
    assert select_current_catalog(parse_published_catalogs(both)) is None
    archived = """
    <select name="catalog">
      <option value="23" selected>2026-2027 Academic Catalog [ARCHIVED CATALOG]</option>
    </select>
    """
    assert select_current_catalog(parse_published_catalogs(archived)) is None


def test_course_snapshot_matches_the_published_block():
    html = """
    <p><h1 id="course_preview_title">ISDA 150 - Topics in Information Science and Data Analytics</h1>
    <em>1-3</em> <em>unit(s)</em><br>
    Current and emerging issues, practices, and technologies relevant to Information and Data Science professionals.
    <br><br><em>Repeatable for up to 9 units when content changes.</em><br><br>
    <strong>Prerequisite(s):</strong> <a href="preview_course_nopop.php?catoid=24&coid=9">ISDA 110</a>.<br>
    <strong>Grading:</strong> Letter graded.<br>
    <strong>Note(s):</strong> Topics vary by semester.<br>
    <p>Class Schedule | Syllabus Information | University Bookstore</p>
    </p>
    """
    url = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=24&coid=300001"
    text = extract_course_snapshot(html, url, "2027-09-01")
    assert text == (
        "ISDA 150 - Topics in Information Science and Data Analytics\n"
        "1-3 unit(s)\n"
        "Prerequisite(s): ISDA 110.\n"
        "Grading: Letter graded.\n"
        "Note(s): Topics vary by semester.\n"
        "Current and emerging issues, practices, and technologies relevant to "
        "Information and Data Science professionals.\n"
        "Repeatable for up to 9 units when content changes.\n"
        f"Source URL: {url}\n"
        "Retrieved: 2027-09-01\n"
    )
    assert (
        extract_course_snapshot("<html><h1>Not a course</h1></html>", url, "2027-09-01")
        == ""
    )


def test_info_course_snapshot_and_isda_listing_are_supported():
    info_html = """
    <p><h1 id="course_preview_title">INFO 210 - Reference Services</h1>
    <em>3</em> <em>unit(s)</em><br>Reference description.<br><br>
    <strong>Prerequisite(s):</strong> INFO 202.<br>
    <p>Class Schedule | Syllabus Information</p></p>
    """
    info_url = "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=1"
    assert extract_course_snapshot(info_html, info_url, "2026-10-15").startswith(
        "INFO 210 - Reference Services\n3 unit(s)\nPrerequisite(s): INFO 202.\n"
    )

    listing_html = """
    <a href="preview_course_nopop.php?catoid=23&coid=1">ISDA 20A - Query Languages</a>
    <a href="preview_course_nopop.php?catoid=23&coid=2">ISDA 194 - Professional Experience: Internship</a>
    """
    assert course_listing_entries(listing_html) == [
        ("ISDA 20A", "Query Languages"),
        ("ISDA 194", "Professional Experience: Internship"),
    ]
    listing = extract_course_listing_snapshot(
        listing_html, "https://catalog.sjsu.edu/list", "2026-10-15", "2026-2027"
    )
    assert "lists these 2 ISDA courses" in listing
    assert "ISDA 194 - Professional Experience: Internship" in listing


def test_program_snapshot_keeps_the_core_heading():
    html = """
    <div id="acalog-content">
      <h1>Information Science and Data Analytics, BS</h1>
      <p>The Bachelor of Science prepares students to work with data.</p>
      <h2>Core Courses (36 units)</h2>
      <p>ISDA 100 - Technical Writing for Information (3)</p>
    </div>
    """
    url = "https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=21000"
    text = extract_program_snapshot(html, url, "2027-09-01", "2027-2028")
    assert text.startswith("2027-2028 Academic Catalog\n")
    assert "Information Science and Data Analytics, BS" in text
    assert "\nCore Courses (36 units)\n" in text
    assert "ISDA 100 - Technical Writing for Information (3)" in text
    assert f"Source URL: {url}" in text
    assert text.endswith("Retrieved: 2027-09-01\n")


def _course_html(code, coid):
    return f"""
    <p><h1 id="course_preview_title">{code} - Sample Course</h1>
    <em>3</em> <em>unit(s)</em><br>
    Sample description for {code}.
    <strong>Grading:</strong> Letter graded.<br>
    </p>
    """


def _program_html(title, courses):
    items = "\n".join(
        f'<li><a href="preview_course_nopop.php?catoid=24&coid={coid}">{code} - Sample Course</a></li>'
        for code, coid in courses
    )
    return f"""
    <div id="acalog-content">
      <h1>{title}</h1>
      <p>Information Science and Data Analytics major requirements.</p>
      <h2>Core Courses (36 units)</h2>
      <ul>{items}</ul>
    </div>
    """


def _pages():
    courses = [(f"ISDA {100 + index}", 300000 + index) for index in range(8)]
    home = """
    <a href="/content.php?catoid=24&navoid=1">Academic Programs</a>
    <a href="/content.php?catoid=24&navoid=2">Course Descriptions</a>
    """
    programs = """
    <a href="preview_program.php?catoid=24&poid=21000&returnto=1">Information Science and Data Analytics, BS</a>
    <a href="/preview_program.php?catoid=24&poid=21001">Information Science and Data Analytics, BS (SJSU Online)</a>
    """
    listing = "\n".join(
        f"<a href=\"#\" onclick=\"showHideCourse(this, '24', '{coid}'); return false;\">{code} - Sample Course</a>"
        for code, coid in courses
    )
    by_url = {
        "https://catalog.sjsu.edu/misc/catalog_list.php?catoid=23": MENU_2027,
        "https://catalog.sjsu.edu/misc/catalog_list.php?catoid=24": MENU_2027,
        "https://catalog.sjsu.edu/index.php?catoid=24": home,
        "https://catalog.sjsu.edu/content.php?catoid=24&navoid=1": programs,
        "https://catalog.sjsu.edu/content.php?catoid=24&navoid=2": listing,
        "https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=21000": _program_html(
            "Information Science and Data Analytics, BS", courses[:4]
        ),
        "https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=21001": _program_html(
            "Information Science and Data Analytics, BS (SJSU Online)", courses[4:]
        ),
    }
    for code, coid in courses:
        url = f"https://catalog.sjsu.edu/preview_course_nopop.php?catoid=24&coid={coid}"
        by_url[url] = _course_html(code, coid)
    return by_url


def _repo(tmp_path: Path) -> Path:
    identity = {
        "current_catoid": "23",
        "current_year": "2026-2027",
        "years_by_catoid": {"23": "2026-2027", "17": "2025-2026", "10": "MyRoadmaps"},
    }
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "catalog_identity.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )
    docs = tmp_path / "data" / "advising_docs"
    (docs / "txt").mkdir(parents=True)
    (docs / "meta").mkdir()
    (docs / "scrape").mkdir()
    (docs / "txt" / "web-old.txt").write_text(
        "older catalog snapshot\n", encoding="utf-8"
    )
    (docs / "scrape" / "seed_urls.txt").write_text(
        "https://ischool.sjsu.edu/bsisda-advising\n"
        "https://catalog.sjsu.edu/preview_course_nopop.php?catoid=23&coid=1\n",
        encoding="utf-8",
    )
    (docs / "scrape" / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "documents": [
                    {
                        "doc_id": "web-old",
                        "source_url": "https://ischool.sjsu.edu/bsisda-advising",
                        "title": "Advising",
                        "content_sha256": "abc",
                        "programs": ["ISDA"],
                        "topics": ["advising"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    (tmp_path / ".env.example").write_text(env, encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    return tmp_path


def _fetch(pages, fail=None):
    calls = []

    def fetch(url):
        calls.append(url)
        if fail and fail(url):
            return FetchResult(status=0, error="timed out")
        if url not in pages:
            return FetchResult(status=404, body="We were unable to locate the resource")
        body = pages[url]
        if "gokuProps" in body:
            return FetchResult(status=200, body=body, blocked=True)
        return FetchResult(status=200, body=body)

    fetch.calls = calls
    return fetch


def test_newer_catalog_refreshes_snapshots_and_settings(tmp_path):
    repo = _repo(tmp_path)
    fetch = _fetch(_pages())
    result = check_for_newer_catalog(repo, fetch, today=date(2027, 9, 1))
    assert result.status == "updated"
    assert result.catoid == "24"
    assert result.year == "2027-2028"
    assert result.previous_year == "2026-2027"

    identity = json.loads(
        (repo / "data" / "catalog_identity.json").read_text(encoding="utf-8")
    )
    assert identity["current_catoid"] == "24"
    assert identity["current_year"] == "2027-2028"
    assert identity["years_by_catoid"]["24"] == "2027-2028"
    assert identity["years_by_catoid"]["10"] == "MyRoadmaps"
    assert identity["years_by_catoid"]["23"] == "2026-2027"

    env = (repo / ".env.example").read_text(encoding="utf-8")
    assert "CURRENT_CATALOG_CATOID=24" in env
    assert "CURRENT_CATALOG_YEAR=2027-2028" in env
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "`CURRENT_CATALOG_CATOID` (24)" in readme
    assert "`CURRENT_CATALOG_YEAR` (2027-2028)" in readme

    seeds = (repo / "data" / "advising_docs" / "scrape" / "seed_urls.txt").read_text(
        encoding="utf-8"
    )
    assert "catoid=23&coid=1" in seeds
    assert "https://ischool.sjsu.edu/bsisda-advising" in seeds
    assert "catoid=24&poid=21000" in seeds
    assert "catoid=24&coid=300000" in seeds
    assert (repo / "data" / "advising_docs" / "txt" / "web-old.txt").is_file()

    manifest = json.loads(
        (repo / "data" / "advising_docs" / "scrape" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    urls = {item["source_url"] for item in manifest["documents"]}
    assert "https://ischool.sjsu.edu/bsisda-advising" in urls
    assert "https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=21000" in urls
    course_files = list((repo / "data" / "advising_docs" / "txt").glob("web-*.txt"))
    assert len(course_files) >= 9  # 8 courses + old file, plus programs are web- too
    sample = repo / "data" / "advising_docs" / "meta"
    program_meta = None
    for path in sample.glob("web-*.yml"):
        meta = yaml.safe_load(path.read_text(encoding="utf-8"))
        if meta["source_url"].endswith("poid=21000"):
            program_meta = meta
            text = (
                repo / "data" / "advising_docs" / "txt" / f"{path.stem}.txt"
            ).read_text(encoding="utf-8")
            assert "Core Courses (36 units)" in text
            assert text.endswith("Retrieved: 2027-09-01\n")
    assert program_meta is not None
    assert program_meta["catalog_year"] == "2027-2028"
    assert program_meta["atomic_section"] == "Core Courses (36 units)"
    assert program_meta["programs"] == ["ISDA"]

    again = check_for_newer_catalog(repo, fetch, today=date(2027, 9, 8))
    assert again.status == "unchanged"
    assert "No pull request" in again.message


def test_unchanged_catalog_does_not_write(tmp_path):
    repo = _repo(tmp_path)
    before = (repo / "data" / "catalog_identity.json").read_text(encoding="utf-8")
    pages = {"https://catalog.sjsu.edu/misc/catalog_list.php?catoid=23": MENU_2026}
    result = check_for_newer_catalog(repo, _fetch(pages), today=date(2026, 9, 27))
    assert result.status == "unchanged"
    assert (repo / "data" / "catalog_identity.json").read_text(
        encoding="utf-8"
    ) == before
    assert not (repo / "data" / "advising_docs" / "meta").exists() or not list(
        (repo / "data" / "advising_docs" / "meta").glob("web-*.yml")
    )


def test_unreachable_or_changed_shape_is_a_warning_and_writes_nothing(tmp_path, caplog):
    repo = _repo(tmp_path)
    before = (repo / "data" / "catalog_identity.json").read_text(encoding="utf-8")

    down = check_for_newer_catalog(
        repo,
        _fetch({}, fail=lambda url: True),
        today=date(2027, 9, 1),
    )
    assert down.status == "unavailable"
    assert "could not be read" in down.message

    challenged = {
        "https://catalog.sjsu.edu/misc/catalog_list.php?catoid=23": "<html>gokuProps</html>",
        "https://catalog.sjsu.edu/index.php?catoid=23": "<html>awsWafCookieDomainList</html>",
    }
    blocked = check_for_newer_catalog(repo, _fetch(challenged), today=date(2027, 9, 1))
    assert blocked.status == "unavailable"

    shapeless = {
        "https://catalog.sjsu.edu/misc/catalog_list.php?catoid=23": "<html><p>Catalog</p></html>"
    }
    changed = check_for_newer_catalog(repo, _fetch(shapeless), today=date(2027, 9, 1))
    assert changed.status == "unavailable"
    assert "did not identify" in changed.message
    assert (repo / "data" / "catalog_identity.json").read_text(
        encoding="utf-8"
    ) == before
    assert "No pull request" in caplog.text or changed.message


def test_incomplete_isda_pages_do_not_write(tmp_path):
    repo = _repo(tmp_path)
    before = (repo / "data" / "catalog_identity.json").read_text(encoding="utf-8")
    pages = _pages()
    # Drop every course page so extraction cannot finish.
    pages = {
        url: body
        for url, body in pages.items()
        if "preview_course_nopop.php?catoid=24" not in url
    }
    result = check_for_newer_catalog(repo, _fetch(pages), today=date(2027, 9, 1))
    assert result.status == "unavailable"
    assert (repo / "data" / "catalog_identity.json").read_text(
        encoding="utf-8"
    ) == before


def test_pull_request_body_names_the_new_catalog_year():
    body = pull_request_body(
        CatalogCheckResult(
            status="updated",
            message="Refreshed",
            catoid="24",
            year="2027-2028",
            previous_year="2026-2027",
        )
    )
    assert "2027-2028" in body
    assert "catoid 24" in body
    assert "2026-2027" in body
    assert "/readyz" in body


def test_workflow_opens_a_review_pull_request_and_leaves_main_alone():
    workflow = (ROOT / ".github" / "workflows" / "check-sjsu-catalog.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "cron:" in workflow
    assert "contents: write" in workflow
    assert "pull-requests: write" in workflow
    assert 'startswith("catalog/sjsu-")' in workflow
    assert "gh pr create --base main" in workflow
    assert "git push origin main" not in workflow
    assert "gh pr merge" not in workflow
    assert 'git checkout -b "${branch}"' in workflow
    assert "scripts/check_sjsu_catalog.py" in workflow
    assert "python -m pytest tests/ -q" in workflow


# Synthetic HTML below exercises detection without adding program claims to the corpus.
def _additional_programs(pages):
    listing = 'https://catalog.sjsu.edu/content.php?catoid=24&navoid=1'
    mara = 'https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=22000'
    msis = 'https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=22001'
    pages[listing] += f'''
    <a href="{mara}">Archives and Records Administration, MARA</a>
    <a href="{msis}">Information Science, MS</a>
    <a href="preview_program.php?catoid=24&poid=22002">Informatics, MS</a>
    '''
    pages[mara] = '''<main><h1>Archives and Records Administration, MARA</h1>
    <p>MARA program parser fixture only.</p></main>'''
    return mara, msis


def test_new_program_links_are_scoped_and_official():
    from src.catalog_update import program_links

    pages = _pages()
    mara, msis = _additional_programs(pages)
    html = pages['https://catalog.sjsu.edu/content.php?catoid=24&navoid=1']
    html += '''
    <a href="https://example.org/preview_program.php?catoid=24&poid=1">Information Science, MS</a>
    <a href="preview_program.php?catoid=23&poid=1">Archives and Records Administration, MARA</a>
    <a href="preview_course.php?catoid=24&poid=1">Information Science, MS</a>
    '''
    assert program_links(html, '24', 'MARA') == [mara]
    assert program_links(html, '24', 'MSIS') == [msis]
    assert len(program_links(html, '24')) == 2
    assert program_links(html, '24', 'unknown') == []


def test_refresh_adds_citable_mara_but_never_fetches_or_ingests_msis(tmp_path):
    from hashlib import sha256

    repo = _repo(tmp_path)
    pages = _pages()
    mara, msis = _additional_programs(pages)
    requested = []
    fake_fetch = _fetch(pages)

    def fetch(url):
        requested.append(url)
        assert url != msis
        assert 'poid=22002' not in url
        return fake_fetch(url)

    result = check_for_newer_catalog(repo, fetch, today=date(2027, 9, 1))
    assert result.status == 'updated'
    assert mara in requested
    assert result.msis_program_urls == [msis]
    assert result.as_dict()['msis_program_urls'] == [msis]
    assert msis in pull_request_body(result)
    assert 'MSIS content remains disabled' in result.message
    docs = repo / 'data/advising_docs'
    manifest = json.loads((docs / 'scrape/manifest.json').read_text())
    entry = next(item for item in manifest['documents'] if item['source_url'] == mara)
    meta = yaml.safe_load((docs / 'meta' / f"{entry['doc_id']}.yml").read_text())
    content = (docs / 'txt' / f"{entry['doc_id']}.txt").read_bytes()
    assert entry['programs'] == meta['programs'] == ['MARA']
    assert meta['catalog_year'] == '2027-2028'
    assert meta['source_url'] == mara
    assert sha256(content).hexdigest() == meta['content_sha256'] == entry['content_sha256']
    assert f'Source URL: {mara}' in content.decode()
    assert not any('MSIS' in item['programs'] for item in manifest['documents'])
    assert msis not in (docs / 'scrape/seed_urls.txt').read_text()


def test_unreadable_or_mismatched_mara_aborts_before_any_writes(tmp_path):
    repo = _repo(tmp_path)
    pages = _pages()
    mara, _ = _additional_programs(pages)
    before = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    for body in (None, '<main><h1>Informatics, MS</h1><p>MARA mention</p></main>'):
        if body is None:
            del pages[mara]
        else:
            pages[mara] = body
        result = check_for_newer_catalog(repo, _fetch(pages))
        assert result.status == 'unavailable'
        assert 'MARA program page' in result.message
        assert before == {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob('*') if p.is_file()}


def test_msis_snapshot_extraction_stays_disabled():
    html = '<main><h1>Information Science, MS</h1><p>Parser fixture only.</p></main>'
    assert extract_program_snapshot(html, 'https://catalog.sjsu.edu/preview_program.php?catoid=24&poid=1',
                                    '2027-09-01', '2027-2028', 'MSIS') == ''

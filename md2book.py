import argparse
import html
import mimetypes
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import markdown
import yaml
from ebooklib import epub
from tqdm import tqdm
from weasyprint import CSS, HTML


@dataclass
class BookConfig:
    """Represents configurable book settings with sensible defaults."""

    title: str = "Generated Book"
    author: str = "md2pdf"
    language: str = "en"
    font_family: str = (
        '"Source Han Serif SC", "Noto Serif CJK SC", "STSong", "SimSun", '
        '"Times New Roman", "Georgia", "Palatino Linotype", "STIX Two Text", serif'
    )
    heading_font_family: str = (
        '"Source Han Sans SC", "Noto Sans CJK SC", "PingFang SC", "Hiragino Sans GB", '
        '"Microsoft YaHei", "Heiti SC", "Segoe UI", "Helvetica Neue", "Roboto", "Arial", sans-serif'
    )
    chapter_title_font_family: str = heading_font_family
    table_font_family: str = (
        '"Kaiti SC", "STKaiti", "KaiTi", "KaiTi_GB2312", "DFKai-SB", serif'
    )
    code_font_family: str = (
        '"Kaiti SC", "STKaiti", "KaiTi", "KaiTi_GB2312", "DFKai-SB", "Courier New", monospace'
    )
    base_font_size: str = "12pt"
    heading_color: str = "#77AAC2"
    heading_color_h1: str = "#77AAC2"
    heading_color_h2: str = "#77AAC2"
    heading_color_h3: str = "#77AAC2"
    text_color: str = "#111111"
    background_color: str = "#ffffff"
    link_color: str = "#1a73e8"
    code_background_color: str = "#f5f5f5"
    code_border_color: str = "#e0e0e0"
    table_cell_padding: str = "6px 12px"
    line_height: float = 1.6
    margin_top: str = "30mm"
    margin_bottom: str = "30mm"
    margin_left: str = "25mm"
    margin_right: str = "25mm"
    page_size: str = "A4"
    chapter_page_break: bool = True
    toc: bool = True
    extra_css: str = ""
    epub_cover: Optional[str] = None
    pdf_cover: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)
    header_enabled: bool = True
    header_title: Optional[str] = None
    header_chapter: bool = True
    header_font_size: str = "10pt"
    header_border_color: str = "#cccccc"
    url_prefix: str = "https://ddia.qtmuniao.com/#/"
    footer_enabled: bool = True
    footer_font_size: str = "10pt"
    footer_border_color: str = "#cccccc"
    footer_html: str = '<footer style="text-align: center; color: #696773;"><span><a href="https://www.qtmuniao.com/" style="color: #79D9CE;">作者：木鸟杂记</a>&nbsp; ❄ &nbsp; </span><span><a href="https://mp.weixin.qq.com/mp/appmsgalbum?__biz=Mzg5NTcxNzY2OQ==&action=getalbum&album_id=2164896217070206977&scene=126&devicetype=iOS15.4&version=18001d33&lang=zh_CN&nettype=WIFI&ascene=59&session_us=gh_80636260f9f9&fontScale=106&wx_header=3" style="color: #FCD765;">公众号</a>&nbsp; ❄ &nbsp; </span><span><a href="https://distsys.cn/" style="color: #F19A97;">分布式论坛</a>&nbsp; ❄ &nbsp; </span><span><a href="https://xiaobot.net/p/system-thinking" style="color: #77AAC2;">系统技术专栏</a></span></footer>'
    exclude_pages: List[str] = field(default_factory=lambda: ["readme", "sidebar"])

    @classmethod
    def from_yaml(cls, path: Optional[Path]) -> "BookConfig":
        if path is None:
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(**{**cls().__dict__, **data})


@dataclass
class Chapter:
    title: str
    source_path: Path
    html: str
    anchor: str


def slugify_title(title: str) -> str:
    """Create a URL-friendly anchor while keeping non-latin characters intact."""

    slug = re.sub(r"\s+", "-", title.strip())
    slug = re.sub(r"[^\w\-\u0080-\uffff]", "", slug)
    return slug.lower() or "section"


def normalize_heading_ids(
    html: str, level_one: List[Dict[str, str]], anchor: str
) -> Tuple[str, List[Tuple[str, str]]]:
    """Ensure predictable heading ids and return the normalized mapping."""

    normalized: List[Tuple[str, str]] = []
    seen_ids: Dict[str, int] = {}
    for idx, token in enumerate(level_one):
        original_id = token.get("id") or slugify_title(token.get("name", ""))
        heading_text = token.get("name", "")
        base_id = anchor if idx == 0 else f"{anchor}-{original_id}"
        suffix = seen_ids.get(base_id, 0)
        seen_ids[base_id] = suffix + 1
        new_id = base_id if suffix == 0 else f"{base_id}-{suffix}"
        html = re.sub(
            rf'id="{re.escape(original_id)}"',
            f'id="{new_id}"',
            html,
            count=1,
        )
        if idx == 0:
            # Mark the first heading for running headers.
            html = re.sub(
                rf"<h1([^>]*id=\"{re.escape(new_id)}\"[^>]*)>",
                r"<h1\1 class=\"chapter-title\">",
                html,
                count=1,
            )
        normalized.append((new_id, heading_text))

    # Ensure the chapter still exposes a title for headers even without h1.
    if not normalized:
        normalized.append((anchor, ""))

    return html, normalized


def collect_heading_links(html: str) -> List[Tuple[int, str, str]]:
    """Collect normalized level-one and level-two headings for TOC linking."""

    headings: List[Tuple[int, str, str]] = []
    for match in re.finditer(
        r"<h([1-2])[^>]*id=\"([^\"]+)\"[^>]*>(.*?)</h\1>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        level = int(match.group(1))
        text = re.sub(r"<[^>]+>", "", match.group(3)).strip()
        headings.append((level, match.group(2), text))
    return headings


def build_nested_toc(headings: List[Tuple[int, str, str]]) -> str:
    """Build a nested unordered list from heading tuples (level, id, text)."""

    if not headings:
        return ""

    root = {"level": 1, "children": []}
    stack = [root]

    for level, hid, text in headings:
        node = {"level": level, "id": hid, "text": text, "children": []}
        while len(stack) > 1 and level <= stack[-1]["level"]:
            stack.pop()
        stack[-1]["children"].append(node)
        stack.append(node)

    def render_children(nodes: List[Dict[str, object]]) -> str:
        if not nodes:
            return ""

        items: List[str] = []
        for node in nodes:
            children_html = render_children(node["children"])
            items.append(
                f"<li><a href='#{node['id']}'>{node['text']}</a>{children_html}</li>"
            )

        return f"<ul>{''.join(items)}</ul>"

    def render_nodes(nodes: List[Dict[str, object]]) -> str:
        html_parts: List[str] = []

        for node in nodes:
            children_html = render_children(node["children"])

            if node["level"] == 1:
                html_parts.append(
                    f"<h2><a href='#{node['id']}'>{node['text']}</a></h2>{children_html}"
                )
            else:
                html_parts.append(
                    f"<li><a href='#{node['id']}'>{node['text']}</a>{children_html}</li>"
                )

        return "".join(html_parts)

    return render_nodes(root["children"])


def discover_images(
    html: str,
    base_dir: Path,
    added_resources: Dict[Path, epub.EpubItem],
) -> List[Tuple[str, epub.EpubItem]]:
    """Find image sources in HTML and prepare EpubImage resources."""

    images: List[Tuple[str, epub.EpubItem]] = []
    for match in re.finditer(r"<img[^>]+src=\"([^\"]+)\"", html, flags=re.IGNORECASE):
        src = match.group(1)
        if (
            src.startswith("http://")
            or src.startswith("https://")
            or src.startswith("data:")
        ):
            continue
        resolved = (base_dir / src).resolve()
        if not resolved.exists():
            continue
        if resolved in added_resources:
            resource = added_resources[resolved]
        else:
            media_type, _ = mimetypes.guess_type(resolved.name)
            file_name = f"images/{len(added_resources) + 1}_{resolved.name}"
            resource = epub.EpubImage(
                uid=file_name,
                file_name=file_name,
                media_type=media_type or "image/jpeg",
                content=resolved.read_bytes(),
            )
            added_resources[resolved] = resource
        images.append((src, resource))
    return images


def discover_markdown_files(paths: Iterable[Path]) -> List[Path]:
    files: List[Path] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() in {".md", ".markdown"}:
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
            files.extend(sorted(path.rglob("*.markdown")))
    unique_files: List[Path] = []
    seen = set()
    for file in files:
        if file not in seen:
            seen.add(file)
            unique_files.append(file)
    return unique_files


def prioritize_files(files: List[Path]) -> List[Path]:
    """Place preface-like files first while keeping original order otherwise."""

    preface_indices = [
        idx for idx, path in enumerate(files) if path.stem.lower() == "preface"
    ]
    if not preface_indices:
        return files

    first_preface = preface_indices[0]
    preface_file = files[first_preface]
    remaining = [file for idx, file in enumerate(files) if idx != first_preface]
    return [preface_file] + remaining


def clone_repository(url: str, workspace: Path) -> Path:
    repo_dir = workspace / "repo"
    subprocess.run(["git", "clone", "--depth", "1", url, str(repo_dir)], check=True)
    return repo_dir


def load_chapters(paths: Sequence[Path]) -> List[Chapter]:
    chapters: List[Chapter] = []
    slug_counts: Dict[str, int] = {}
    for path in paths:
        markdown_text = path.read_text(encoding="utf-8")
        md = markdown.Markdown(
            extensions=[
                "fenced_code",
                "tables",
                "codehilite",
                "toc",
                "attr_list",
            ],
            output_format="html5",
        )
        html = md.convert(markdown_text)
        toc_tokens = md.toc_tokens or []
        level_one = [token for token in toc_tokens if token.get("level") == 1]
        title = (
            level_one[0]["name"] if level_one else path.stem.replace("_", " ").title()
        )
        anchor_base = slugify_title(title)
        anchor_suffix = slug_counts.get(anchor_base, 0)
        slug_counts[anchor_base] = anchor_suffix + 1
        anchor = anchor_base if anchor_suffix == 0 else f"{anchor_base}-{anchor_suffix}"

        html, _ = normalize_heading_ids(html, level_one, anchor)

        chapters.append(
            Chapter(
                title=title,
                source_path=path,
                html=html,
                anchor=anchor,
            )
        )
    return chapters


def build_css(config: BookConfig) -> str:
    header_footer_css = ""
    if config.header_enabled:
        header_footer_css += f"""
    h1.chapter-title {{
        string-set: chapter-title content();
    }}

    .chapter-header-title {{
        string-set: chapter-title content();
        position: running(page-header);
        font-size: {config.header_font_size};
        text-align: center;
        display: inline-block;
        padding-bottom: 6px;
        border-bottom: 1px dashed {config.header_border_color};
        visibility: visible;
        height: auto;
        overflow: visible;
        margin: 0 auto;
    }}

    @page {{
        @top-center {{
            content: element(page-header);
            text-align: center;
        }}
    }}

    @page:left {{
        @top-center {{
            content: element(page-header);
            text-align: center;
        }}
    }}

    @page:right {{
        @top-center {{
            content: element(page-header);
            text-align: center;
        }}
    }}
        """

    if config.footer_enabled:
        header_footer_css += f"""
    .page-footer {{
        position: running(page-footer);
        font-size: {config.footer_font_size};
        border-top: 1px dashed {config.footer_border_color};
        padding-top: 6px;
        width: 100%;
        box-sizing: border-box;
        display: block;
    }}

    @page {{
        @bottom-center {{
            content: element(page-footer);
        }}
    }}
        """

    return f"""
    @page {{
        size: {config.page_size};
        margin: {config.margin_top} {config.margin_right} {config.margin_bottom} {config.margin_left};
        counter-increment: page;
    }}

    @page:left {{
        @top-left {{
            content: counter(page);
            font-size: {config.header_font_size};
        }}
    }}

    @page:right {{
        @top-right {{
            content: counter(page);
            font-size: {config.header_font_size};
        }}
    }}

    body {{
        font-family:
          {config.font_family};
        font-size: {config.base_font_size};
        color: {config.text_color};
        background: {config.background_color};
        line-height: {config.line_height};
        counter-reset: page 0;
    }}

    h1 {{
        font-family:
          {config.heading_font_family};
        color: {config.heading_color_h1};
        margin-top: 1.4em;
    }}

    h2 {{
        font-family:
          {config.heading_font_family};
        color: {config.heading_color_h2};
        margin-top: 1.4em;
    }}

    h3 {{
        font-family:
          {config.heading_font_family};
        color: {config.heading_color_h3};
        margin-top: 1.2em;
    }}

    h4, h5, h6 {{
        font-family:
          {config.heading_font_family};
        color: {config.heading_color};
        margin-top: 1.1em;
    }}

    a {{
        color: {config.link_color};
        text-decoration: none;
    }}

    em, i {{
        font-style: italic;
        font-family:
          {config.font_family};
        font-synthesis: style;
    }}

    pre, code {{
        font-family:
          {config.code_font_family};
    }}

    pre {{
        background: {config.code_background_color};
        border: 1px dashed {config.code_border_color};
        padding: 12px;
        border-radius: 4px;
        overflow-x: auto;
        font-size: clamp(10px, 0.95em, 1em);
        box-sizing: border-box;
    }}

    pre code {{
        display: block;
        font-size: clamp(10px, 0.9em, 0.95em);
        white-space: pre-wrap;
        word-break: break-word;
    }}

    blockquote {{
        border-left: 4px solid {config.link_color};
        padding-left: 12px;
        color: #555;
        margin-left: 0;
    }}

    hr {{
        border: none;
        border-top: 1px dashed {config.code_border_color};
        margin: 24px 0;
    }}

    table {{
        border-collapse: collapse;
        width: 100%;
    }}

    table, th, td {{
        border: 1px solid {config.code_border_color};
    }}

    th, td {{
        padding: {config.table_cell_padding};
        font-family:
          {config.table_font_family};
    }}

    img {{
        display: block;
        margin-left: auto;
        margin-right: auto;
        max-width: 100%;
    }}

    figure {{
        text-align: center;
    }}

    .book-title {{
        text-align: center;
        margin-top: 60px;
        font-size: 2.4em;
    }}

    .book-author {{
        text-align: center;
        color: #666;
        margin-bottom: 40px;
    }}

    .book-author p {{
        margin: 0;
    }}

    .chapter {{
        page-break-before: {"always" if config.chapter_page_break else "auto"};
        page-break-after: auto;
    }}

    .pdf-cover {{
        page-break-after: always;
        text-align: center;
    }}

    .pdf-cover img {{
        max-width: 100%;
        height: auto;
    }}

    .no-page-number {{
        page: no-number;
    }}

    @page no-number {{
        @top-right {{
            content: none;
        }}
        @top-left {{
            content: none;
        }}
        counter-increment: none;
    }}

    .toc-title {{
        font-size: 1.6em;
        font-weight: bold;
        margin-bottom: 10px;
    }}

    .toc-list {{
        list-style: none;
        padding-left: 0;
    }}

    .toc-list li {{
        margin: 6px 0;
    }}

    .toc-list ul {{
        padding-left: 20px;
    }}

    .toc {{
        page-break-after: always;
    }}

    .toc a::after {{
        content: leader('.') target-counter(attr(href), page);
        float: right;
        color: {config.text_color};
    }}

    .chapter-title {{
        text-align: center;
        font-family:
          {config.chapter_title_font_family};
    }}

    .chapter > h1:first-of-type {{
        text-align: center;
    }}

    .chapter-header-title {{
        string-set: chapter-title content();
        visibility: hidden;
        height: 0;
        overflow: hidden;
    }}

    .chapter-header-title a {{
        color: inherit;
        text-decoration: none;
    }}

    {header_footer_css}
    {config.extra_css}
    """


def render_html(chapters: Sequence[Chapter], config: BookConfig) -> Tuple[str, Path]:
    if not chapters:
        raise ValueError("No Markdown content found to render")

    base_path = chapters[0].source_path.parent
    toc_headings: List[Tuple[int, str, str]] = []
    author_html = markdown.markdown(
        config.author, extensions=["attr_list"], output_format="html5"
    )
    body_parts: List[str] = []

    if config.pdf_cover:
        cover_path = Path(config.pdf_cover).expanduser()
        if not cover_path.is_absolute():
            cover_path = (base_path / cover_path).resolve()
        if not cover_path.exists():
            raise FileNotFoundError(f"PDF cover image not found: {cover_path}")
        body_parts.append(
            f"<div class='pdf-cover no-page-number'><img src='{cover_path.as_uri()}' alt='Book cover'></div>"
        )

    body_parts.append(
        "<div class='book-meta no-page-number'>"
        f"<h1 class='book-title'>{config.title}</h1>"
        f"<div class='book-author'>{author_html}</div>"
        "</div>"
    )

    if config.footer_enabled:
        body_parts.append(f"<div class='page-footer'>{config.footer_html}</div>")

    toc_insert_index = len(body_parts)

    for idx, chapter in enumerate(
        tqdm(chapters, desc="Rendering chapters", unit="chapter"), start=1
    ):
        anchor = chapter.anchor
        chapter_html = chapter.html
        if "chapter-title" not in chapter_html:
            chapter_html = (
                f"<h1 id='{anchor}' class='chapter-title'>{chapter.title}</h1>"
                + chapter_html
            )
        if config.toc:
            chapter_headings = collect_heading_links(chapter_html)
            if not chapter_headings:
                chapter_headings = [(1, anchor, chapter.title)]
            toc_headings.extend(chapter_headings)
        chapter_url = f"{config.url_prefix.rstrip('/')}/{chapter.anchor}"
        safe_title = html.escape(chapter.title)
        header_marker = (
            "<div class='chapter-header-title'>"
            f"<a href='{chapter_url}'>{safe_title}</a>"
            "</div>"
        )
        body_parts.append(
            f"<section id='{anchor}' class='chapter'>{header_marker}{chapter_html}</section>"
        )

    if config.toc:
        toc_html = (
            "<div class='toc'><div class='toc-title'>章节目录</div>"
            + build_nested_toc(toc_headings)
            + "</div>"
        )
        body_parts.insert(
            toc_insert_index,
            toc_html.replace("class='toc'", "class='toc no-page-number'"),
        )

    content = "".join(body_parts)
    return (
        f"<html><head><meta charset='utf-8'></head><body>{content}</body></html>",
        base_path,
    )


def convert_to_pdf(
    html_content: str, css: str, output_path: Path, base_url: Path
) -> None:
    HTML(string=html_content, base_url=str(base_url)).write_pdf(
        stylesheets=[CSS(string=css)], target=str(output_path)
    )


def convert_to_epub(
    chapters: Sequence[Chapter], config: BookConfig, css: str, output_path: Path
) -> None:
    book = epub.EpubBook()
    book.set_title(config.title)
    book.set_language(config.language)
    book.add_author(config.author)

    for key, value in config.metadata.items():
        book.add_metadata("DC", key, value)

    if config.epub_cover and Path(config.epub_cover).exists():
        book.set_cover(
            Path(config.epub_cover).name, Path(config.epub_cover).read_bytes()
        )

    style = epub.EpubItem(
        uid="style_nav", file_name="style/nav.css", media_type="text/css", content=css
    )
    book.add_item(style)

    epub_chapters = []
    added_resources: Dict[Path, epub.EpubItem] = {}
    for idx, chapter in enumerate(chapters, start=1):
        chapter_html = chapter.html
        for original_src, resource in discover_images(
            chapter_html, chapter.source_path.parent, added_resources
        ):
            book.add_item(resource)
            chapter_html = chapter_html.replace(original_src, resource.file_name)

        item = epub.EpubHtml(
            title=chapter.title,
            file_name=f"chap_{idx}.xhtml",
            lang=config.language,
            content=chapter_html,
        )
        item.add_item(style)
        book.add_item(item)
        epub_chapters.append(item)

    book.toc = tuple(epub_chapters)
    book.spine = ["nav"] + epub_chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(output_path), book)


def resolve_sources(
    sources: Sequence[str],
) -> Tuple[List[Path], Optional[tempfile.TemporaryDirectory]]:
    temp_dir: Optional[tempfile.TemporaryDirectory] = None
    paths: List[Path] = []

    for src in sources:
        if (
            src.startswith("http://")
            or src.startswith("https://")
            or src.startswith("git@")
        ):
            temp_dir = tempfile.TemporaryDirectory()
            repo_path = clone_repository(src, Path(temp_dir.name))
            paths.append(repo_path)
        else:
            paths.append(Path(src).expanduser().resolve())

    return paths, temp_dir


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Markdown sources into PDF or EPUB books."
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="Markdown files, directories, or a GitHub repository URL.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help="Path to YAML configuration file. If omitted defaults are used.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("book"),
        help="Base output path without extension.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["pdf", "epub", "both", "html", "all"],
        default="pdf",
        help="Choose the output format.",
    )
    parser.add_argument(
        "--pdf-cover",
        type=Path,
        default=None,
        help="Path to an image that will be placed on the first PDF page as a cover.",
    )
    return parser.parse_args(argv)


def convert_to_html(
    html_content: str, css: str, output_path: Path, base_url: Path
) -> None:
    head_injection = f"<style>{css}</style><base href='{base_url.as_uri()}/'>"
    if "</head>" in html_content:
        styled_html = html_content.replace("</head>", f"{head_injection}</head>", 1)
    else:
        styled_html = (
            f"<head><meta charset='utf-8'>{head_injection}</head>" + html_content
        )
    output_path.write_text(styled_html, encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    config = BookConfig.from_yaml(args.config)
    if args.pdf_cover is not None:
        config.pdf_cover = str(args.pdf_cover.expanduser())
    source_paths, temp_dir = resolve_sources(args.sources)

    try:
        markdown_files = discover_markdown_files(source_paths)
        if not markdown_files:
            raise SystemExit("No Markdown files found in provided sources")

        markdown_files = [
            path
            for path in markdown_files
            if path.stem.lower() not in {page.lower() for page in config.exclude_pages}
        ]
        if not markdown_files:
            raise SystemExit("No Markdown files found after applying exclusions")
        markdown_files = prioritize_files(markdown_files)

        chapters = load_chapters(markdown_files)
        css = build_css(config)

        html_content: Optional[str] = None
        base_url: Optional[Path] = None
        if args.format in {"pdf", "both", "html", "all"}:
            html_content, base_url = render_html(chapters, config)

        if args.format in {"pdf", "both", "all"} and html_content and base_url:
            pdf_path = args.output.with_suffix(".pdf")
            convert_to_pdf(html_content, css, pdf_path, base_url)
            print(f"PDF created at {pdf_path}")

        if args.format in {"html", "all"} and html_content and base_url:
            html_path = args.output.with_suffix(".html")
            convert_to_html(html_content, css, html_path, base_url)
            print(f"HTML created at {html_path}")

        if args.format in {"epub", "both", "all"}:
            epub_path = args.output.with_suffix(".epub")
            convert_to_epub(chapters, config, css, epub_path)
            print(f"EPUB created at {epub_path}")

    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


if __name__ == "__main__":
    main()

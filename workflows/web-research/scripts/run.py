"""
CLI entry point for the web-research workflow.
Wraps the shared skill at skills/web-research/scripts/research.py.

Usage:
    python workflows/web-research/scripts/run.py --topic "your topic" [options]
    python workflows/web-research/scripts/run.py --topic "your topic" --dry-run
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone

# Locate project root relative to this script
_WORKFLOW_DIR = Path(__file__).parent.parent          # workflows/web-research/
_PROJECT_ROOT = _WORKFLOW_DIR.parent.parent           # AI-OS/
_SKILL_SCRIPTS = _PROJECT_ROOT / 'skills' / 'web-research' / 'scripts'

sys.path.insert(0, str(_SKILL_SCRIPTS))

from research import research  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description='Research a topic and save a package ready for Biblio to turn into a report.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py --topic "UK AI regulation"
  python run.py --topic "React vs Vue 2026" --type blog-post --words 800 --tone conversational
  python run.py --topic "quantum computing" --include arxiv,wikipedia --words 2000 --type article
  python run.py --topic "AI news this week" --rss-category ai-news --sources 10
  python run.py --topic "example" --dry-run
        """,
    )

    # Research
    parser.add_argument('--topic', required=True, help='Research topic or question')
    parser.add_argument('--sources', type=int, default=8, metavar='N',
                        help='Max number of sources to gather (default: 8)')
    parser.add_argument('--include', metavar='SOURCES',
                        help='Comma-separated source names to include '
                             '(wikipedia, arxiv, semantic_scholar, stackexchange, '
                             'hackernews, rss, devto, reddit, scraper)')
    parser.add_argument('--exclude', metavar='SOURCES',
                        help='Comma-separated source names to skip')
    parser.add_argument('--rss-category', metavar='CATEGORY',
                        help='RSS feed category from rss_feeds.yaml '
                             '(ai-news, tech-general, uk-news, science, business, developer)')
    parser.add_argument('--urls', metavar='URLS',
                        help='Comma-separated URLs to scrape directly')

    # Output shaping
    parser.add_argument('--type', dest='content_type', default='article',
                        choices=['social-post', 'blog-post', 'article', 'briefing', 'newsletter', 'summary'],
                        help='Content type for the report (default: article)')
    parser.add_argument('--words', type=int, default=1000, metavar='N',
                        help='Target word count for the report (default: 1000)')
    parser.add_argument('--tone', default='informational',
                        choices=['formal', 'conversational', 'journalistic', 'academic', 'casual'],
                        help='Writing tone (default: informational)')
    parser.add_argument('--audience', default='general',
                        choices=['general', 'technical', 'executive', 'academic'],
                        help='Target audience (default: general)')
    parser.add_argument('--style', default='informational',
                        choices=['informational', 'analytical', 'comparative', 'summary'],
                        help='Report style (default: informational)')

    # Quality
    parser.add_argument('--citations', default='inline',
                        choices=['inline', 'endnotes', 'none'],
                        help='Citation format (default: inline)')
    parser.add_argument('--confidence-markers', action='store_true',
                        help='Ask Biblio to mark each claim with a confidence indicator')
    parser.add_argument('--readability-check', action='store_true',
                        help='Run readability scoring on the research package')
    parser.add_argument('--virality', action='store_true',
                        help='Ask Biblio to optimise the report for engagement and shareability')

    # Image prompt
    parser.add_argument('--image-prompt', action='store_true',
                        help='After the report brief, generate an image prompt via the image-prompt skill')
    parser.add_argument('--platform', default='linkedin',
                        choices=['linkedin', 'twitter', 'instagram-square', 'instagram-portrait', 'blog'],
                        help='Target platform for the image prompt (default: linkedin)')

    # File output
    parser.add_argument('--output', metavar='FILENAME',
                        help='Custom filename for the research package (default: auto-generated)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview what would happen without making any changes')

    args = parser.parse_args()

    # Parse list args
    include = [s.strip() for s in args.include.split(',')] if args.include else None
    exclude = [s.strip() for s in args.exclude.split(',')] if args.exclude else []
    scrape_urls = [u.strip() for u in args.urls.split(',')] if args.urls else None

    # Build output paths
    outputs_dir = _WORKFLOW_DIR / 'outputs'
    slug = _slugify(args.topic)
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    package_name = args.output or f'research-{slug}-{date_str}.json'
    report_name = package_name.replace('research-', 'report-').replace('.json', '.md')
    package_path = outputs_dir / package_name
    report_path = outputs_dir / report_name

    if args.dry_run:
        print('[DRY RUN] No files will be created or modified.\n')
        print(f'  Topic:       {args.topic}')
        print(f'  Sources:     up to {args.sources}')
        print(f'  Include:     {include or "all defaults"}')
        print(f'  Exclude:     {exclude or "none"}')
        print(f'  RSS cat:     {args.rss_category or "none"}')
        print(f'  Scrape URLs: {scrape_urls or "none"}')
        print(f'  Type:        {args.content_type}')
        print(f'  Words:       {args.words}')
        print(f'  Tone:        {args.tone}')
        print(f'  Audience:    {args.audience}')
        print(f'  Style:       {args.style}')
        print(f'  Citations:   {args.citations}')
        print(f'  Confidence:  {args.confidence_markers}')
        print(f'  Virality:    {args.virality}')
        print(f'  Image prompt:{args.image_prompt} (platform: {args.platform})')
        print(f'  Package:     {package_path}')
        print(f'  Report:      {report_path}')
        return

    print(f'\nBook Dragon — Web Research')
    print(f'Topic: "{args.topic}"')
    print(f'Sources: up to {args.sources} | Include: {include or "all defaults"} | Exclude: {exclude or "none"}\n')

    # Run research
    package = research(
        topic=args.topic,
        sources=args.sources,
        include=include,
        exclude=exclude,
        rss_category=args.rss_category,
        scrape_urls=scrape_urls,
        content_type=args.content_type,
        words=args.words,
        tone=args.tone,
        audience=args.audience,
        style=args.style,
        citations=args.citations,
        confidence_markers=args.confidence_markers,
        readability_check=args.readability_check,
        virality=args.virality,
    )

    # Save package
    outputs_dir.mkdir(exist_ok=True)
    with open(package_path, 'w', encoding='utf-8') as f:
        json.dump(package, f, indent=2, ensure_ascii=False)

    # Print results summary
    print(f'\nResearch complete.')
    print(f'  Sources succeeded: {package["source_count"]} '
          f'({", ".join(package["source_status"]["succeeded"]) or "none"})')
    print(f'  Tier summary:      {package["tier_summary"]}')
    print(f'  Corroboration:     {package["corroboration"]["note"]}')
    print(f'  Package saved to:  {package_path}')

    # Surface any quality flags prominently — the user needs to see these
    quality_flags = package.get('quality_flags', [])
    source_status = package.get('source_status', {})

    if quality_flags:
        print('\n' + '!' * 60)
        print('[!] SOURCE QUALITY FLAGS')
        print('!' * 60)
        for flag in quality_flags:
            flag_type, source, detail = (flag.split(' | ', 2) + [''])[:3]
            if flag_type == 'QUOTA_EXCEEDED':
                print(f'  QUOTA EXCEEDED  — {source}')
                print(f'                    {detail}')
            elif flag_type == 'AUTH_ERROR':
                print(f'  AUTH ERROR      — {source}')
                print(f'                    {detail}')
            elif flag_type == 'UNAVAILABLE':
                print(f'  UNAVAILABLE     — {source}')
                print(f'                    {detail}')
        if source_status.get('quota_exceeded') or source_status.get('auth_error'):
            print()
            print('  The research package may have reduced coverage.')
            print('  Check the quality_flags field in the JSON for full detail.')
        print('!' * 60)

    # Print Biblio brief
    print('\n' + '-' * 60)
    print('BIBLIO REPORT BRIEF')
    print('-' * 60)
    print(f'Research package: {package_path}')
    print(f'Write a {args.content_type} of approximately {args.words} words.')
    print(f'Tone: {args.tone} | Audience: {args.audience} | Style: {args.style}')
    print(f'Citations: {args.citations}', end='')
    if args.confidence_markers:
        print(' | Include confidence markers on claims', end='')
    if args.virality:
        print(' | Optimise for engagement and shareability', end='')
    print()
    if quality_flags:
        print()
        print('[!] NOTE - the following sources were unavailable during research:')
        for flag in quality_flags:
            parts = flag.split(' | ', 2)
            flag_type = parts[0] if parts else flag
            source = parts[1] if len(parts) > 1 else ''
            detail = parts[2] if len(parts) > 2 else ''
            print(f'  {flag_type}: {source} — {detail}')
        print('Please acknowledge this in the report and adjust confidence')
        print('markers accordingly. Do not assert claims as well-sourced if')
        print('key Tier 1 sources were absent.')
    print(f'\nSave the report to: {report_path}')

    if args.image_prompt:
        print()
        print('IMAGE PROMPT:')
        print(f'Apply the image-prompt skill (skills/image-prompt/SKILL.md) to generate')
        print(f'an image to accompany the {args.platform} version of this content.')
        print(f'Follow the full decision matrix and conversation flow in the skill spec:')
        print(f'  1. Analyse the content and state your image type recommendation + reason.')
        print(f'  2. Ask whether to proceed with that or switch to an AI-generated prompt.')
        print(f'  3. Deliver the output for the chosen path.')

    print('-' * 60)

    _append_log(args.topic, package['source_count'], str(package_path))


def _slugify(text):
    import re
    slug = text.lower().replace(' ', '-')
    slug = re.sub(r'[^a-z0-9-]', '', slug)
    return slug[:40].rstrip('-')


def _append_log(topic, source_count, package_path):
    log_path = _WORKFLOW_DIR / 'LOG.md'
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    entry = (
        f'\n[{timestamp}] | Actor: Biblio | Action: ran | '
        f'Note: Researched "{topic}". '
        f'{source_count} sources gathered. Package: {package_path}.'
    )
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(entry)


if __name__ == '__main__':
    main()

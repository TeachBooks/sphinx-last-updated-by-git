"""Get the "last updated" time for each Sphinx page from Git."""
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
import subprocess

from sphinx.locale import _, get_translation
from sphinx.util.i18n import format_date
from sphinx.util.logging import getLogger
from sphinx.util.matching import Matcher
try:
    from sphinx.util.display import status_iterator
except ImportError:
    # For older Sphinx versions, will be removed in Sphinx 8:
    from sphinx.util import status_iterator


__version__ = '0.3.8'


logger = getLogger(__name__)

# Translation function for this extension's own messages (domain-aware)
MESSAGE_CATALOG_NAME = 'sphinx_last_updated_by_git'
translate = get_translation(MESSAGE_CATALOG_NAME)


def update_file_dates(
        git_dir, exclude_commits, file_dates, first_parent,
        show_merge_commits):
    """Ask Git for "author date" of given files in given directory.

    A git subprocess is executed at most three times:

    * First, to check which of the files are even managed by Git.
    * With only those files (if any), a "git log" is created and parsed
      until all requested files have been found.
    * If the root commit is reached (i.e. there is at least one of the
      requested files that has never been edited since the root commit),
      git is called again to check whether the repo is "shallow".

    """
    requested_files = set(file_dates)
    assert requested_files

    existing_files = subprocess.check_output(
        [
            'git', 'ls-tree', '--name-only', '-z', 'HEAD',
            '--', *requested_files
        ],
        cwd=git_dir,
        stderr=subprocess.PIPE,
    ).rstrip().rstrip(b'\0')
    if not existing_files:
        return  # None of the requested files are under version control
    existing_files = existing_files.decode('utf-8').split('\0')
    requested_files.intersection_update(existing_files)
    assert requested_files

    git_log_args = [
        'git', 'log', '--pretty=format:%n%at%x00%H%x00%P%x00%aN',
        '--author-date-order', '--relative', '--name-only',
        '--no-show-signature', '-z'
    ]
    if show_merge_commits:
        git_log_args.append('-m')
    if first_parent:
        git_log_args.append('--first-parent')
    git_log_args.extend(['--', *requested_files])

    process = subprocess.Popen(
        git_log_args,
        cwd=git_dir,
        stdout=subprocess.PIPE,
        # NB: We ignore stderr to avoid deadlocks when reading stdout
    )
    with process:
        parse_log(process.stdout, requested_files,
                  git_dir, exclude_commits, file_dates)
        # We don't need the rest of the log if there's something left:
        process.terminate()


def parse_log(stream, requested_files, git_dir, exclude_commits, file_dates):
    requested_files = set(f.encode('utf-8') for f in requested_files)

    line0 = stream.readline()

    # First line is blank
    assert not line0.rstrip(), 'unexpected git output in {}: {}'.format(
        git_dir, line0)

    pending_header = None
    while requested_files:
        # Use pending_header if we read ahead in the previous iteration
        line1 = (
            pending_header if pending_header is not None
            else stream.readline()
        )
        pending_header = None

        if not line1:
            msg = 'end of git log in {}, unhandled files: {}'
            assert exclude_commits, msg.format(
                git_dir, requested_files)
            msg = 'unhandled files in {}: {}, due to excluded commits: {}'
            logger.warning(
                msg.format(git_dir, requested_files, exclude_commits),
                type='git', subtype='unhandled_files')
            break
        pieces = line1.rstrip().split(b'\0')
        # Git outputs 3 pieces for regular commits, but 4 for merge commits
        # (with trailing NUL) when -m is not used. The 4th piece is empty.
        assert len(pieces) in (4, 5), 'invalid git info in {}: {}'.format(
            git_dir, line1)
        timestamp, commit, parent_commits, author = pieces[:4]
        line2 = stream.readline().rstrip()

        # Without -m, merge commits have no file list. If line2 doesn't end
        # with NUL, it's the next commit header, not a file list.
        if not line2.endswith(b'\0'):
            # Save it as the next header and skip this commit
            pending_header = line2
            continue

        line2 = line2.rstrip(b'\0')
        if not line2:
            # Explicit empty file list: skip this commit
            continue
        changed_files = line2.split(b'\0')

        if commit in exclude_commits:
            continue

        too_shallow = False
        if not parent_commits:
            is_shallow = subprocess.check_output(
                # --is-shallow-repository is available since Git 2.15.
                ['git', 'rev-parse', '--is-shallow-repository'],
                cwd=git_dir,
                stderr=subprocess.PIPE,
            ).rstrip()
            if is_shallow == b'true':
                too_shallow = True

        for file in changed_files:
            try:
                requested_files.remove(file)
            except KeyError:
                continue
            else:
                file_dates[file.decode('utf-8')] = (
                    timestamp, too_shallow, author.decode('utf-8')
                )


def update_file_authors(git_dir, file_list, file_authors):
    """Collect all authors who modified given files (entire history).

    Supports both output shapes from Git for ``--name-only -z``:
    1) author and files on the same record (``author\0file\0file\0``) and
    2) author on one record (``author\0``) followed by a record of files
       (``file\0file\0``). Records are newline (``\n``) separated.
    """
    if not file_list:
        return

    git_log_args = [
        'git', 'log', '--pretty=format:%aN%x00', '--name-only',
        '--no-show-signature', '-z', '--', *file_list
    ]

    process = subprocess.Popen(
        git_log_args,
        cwd=git_dir,
        stdout=subprocess.PIPE,
    )

    with process:
        output = process.stdout.read()
        process.wait()

    # Iterate records separated by newlines, associating an author with the
    # subsequent filenames record when needed.
    records = output.split(b'\n')
    pending_author = None
    for rec in records:
        if not rec:
            continue
        parts = rec.rstrip(b'\0').split(b'\0')
        if not parts:
            continue
        if pending_author is None:
            # Expect an author record; files may be present in same record
            author = parts[0].decode('utf-8', 'replace')
            files = [p for p in parts[1:] if p]
            if not files:
                pending_author = author
                continue
        else:
            # This record should contain files for the pending author
            author = pending_author
            files = [p for p in parts if p]
            pending_author = None
        for fb in files:
            try:
                filename = fb.decode('utf-8')
            except Exception:
                continue
            if filename in file_list and author:
                file_authors[filename].add(author)


def update_file_authors_follow_per_file(git_dir, file_list, file_authors):
    """Collect authors per file using ``git log --follow``.

    This follows renames/moves for each file individually and unions authors.
    It's more expensive (one git call per file) and differs from the
    default batch approach which does not use ``--follow``.
    """
    for filename in file_list:
        try:
            proc = subprocess.run(
                ['git', 'log', '--follow', '--format=%aN', '--', filename],
                cwd=git_dir, check=True, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        authors = set(
            a.strip()
            for a in proc.stdout.decode('utf-8', 'replace').splitlines()
            if a.strip()
        )
        if authors:
            file_authors[filename].update(authors)
            logger.debug(
                'git authors (%d) for %s in %s',
                len(authors), filename, git_dir
            )


def _env_updated(app, env):
    # NB: We call git once per sub-directory, because each one could
    #     potentially be a separate Git repo (or at least a submodule)!

    def to_relpath(f: Path) -> str:
        with suppress(ValueError):
            f = f.relative_to(app.srcdir)
        return str(f)

    src_paths = {}
    src_dates = defaultdict(dict)
    excluded = Matcher(app.config.git_exclude_patterns)
    exclude_commits = set(
        map(lambda h: h.encode('utf-8'), app.config.git_exclude_commits))

    for docname, data in env.git_last_updated.items():
        if data is not None:
            continue  # No need to update this source file
        if excluded(env.doc2path(docname, False)):
            continue
        srcfile = Path(env.doc2path(docname)).resolve()
        src_dates[srcfile.parent][srcfile.name] = None
        src_paths[docname] = srcfile.parent, srcfile.name

    srcdir_iter = status_iterator(
        src_dates, 'getting Git timestamps for source files... ',
        'fuchsia', len(src_dates), app.verbosity, stringify_func=to_relpath)
    for git_dir in srcdir_iter:
        try:
            update_file_dates(
                git_dir, exclude_commits, src_dates[git_dir],
                first_parent=app.config.git_first_parent,
                show_merge_commits=app.config.git_show_merge_commits)
        except subprocess.CalledProcessError as e:
            msg = 'Error getting data from Git'
            msg += ' (no "last updated" dates will be shown'
            msg += ' for source files from {})'.format(git_dir)
            if e.stderr:
                msg += ':\n' + e.stderr.decode('utf-8')
            logger.warning(msg, type='git', subtype='subprocess_error')
        except FileNotFoundError as e:
            logger.warning(
                '"git" command not found, '
                'no "last updated" dates will be shown',
                type='git', subtype='command_not_found')
            return

    dep_paths = defaultdict(list)
    dep_dates = defaultdict(dict)

    candi_dates = defaultdict(list)
    show_sourcelink = {}

    for docname, (src_dir, filename) in src_paths.items():
        show_sourcelink[docname] = True
        date = src_dates[src_dir][filename]
        if date is None:
            if not app.config.git_untracked_show_sourcelink:
                show_sourcelink[docname] = False
            if not app.config.git_untracked_check_dependencies:
                continue
        else:
            candi_dates[docname].append(date)
        for dep in env.dependencies[docname]:
            # NB: dependencies are relative to srcdir and may contain ".."!
            if excluded(dep):
                continue
            depfile = Path(env.srcdir, dep).resolve()
            if not depfile.exists():
                logger.warning(
                    "Dependency file %r, doesn't exist, skipping",
                    depfile,
                    location=docname,
                    type='git',
                    subtype='dependency_not_found',
                )
                continue
            dep_dates[depfile.parent][depfile.name] = None
            dep_paths[docname].append((depfile.parent, depfile.name))

    depdir_iter = status_iterator(
        dep_dates, 'getting Git timestamps for dependencies... ',
        'turquoise', len(dep_dates), app.verbosity, stringify_func=to_relpath)
    for git_dir in depdir_iter:
        try:
            update_file_dates(
                git_dir, exclude_commits, dep_dates[git_dir],
                first_parent=app.config.git_first_parent,
                show_merge_commits=app.config.git_show_merge_commits)
        except subprocess.CalledProcessError as e:
            pass  # We ignore errors in dependencies

    for docname, deps in dep_paths.items():
        for dep_dir, filename in deps:
            date = dep_dates[dep_dir][filename]
            if date is None:
                continue
            candi_dates[docname].append(date)

    for docname in src_paths:
        timestamps = candi_dates[docname]
        if timestamps:
            # NB: too_shallow is only relevant if it affects the latest date.
            timestamp, too_shallow, author = max(timestamps)
            if too_shallow:
                timestamp = None
                logger.warning(
                    'Git clone too shallow', location=docname,
                    type='git', subtype='too_shallow')
        else:
            timestamp = None
            author = None
        env.git_last_updated[docname] = (
            timestamp, show_sourcelink[docname], author
        )

    # Optionally collect all authors for each file
    if app.config.git_show_all_authors:
        all_authors = defaultdict(set)

        # Build a de-duplicated set of files per repo dir (sources + deps)
        authors_targets = defaultdict(set)
        for git_dir, files in src_dates.items():
            for f, data in files.items():
                if data:
                    authors_targets[git_dir].add(f)
        for git_dir, files in dep_dates.items():
            for f, data in files.items():
                if data:
                    authors_targets[git_dir].add(f)

        # Single progress iterator over combined targets
        author_iter = status_iterator(
            authors_targets,
            'collecting Git authors (following renames)... ',
            'fuchsia', len(authors_targets), app.verbosity,
            stringify_func=to_relpath)
        for git_dir in author_iter:
            files_to_check = sorted(authors_targets[git_dir])
            if not files_to_check:
                continue
            try:
                update_file_authors_follow_per_file(
                    git_dir, files_to_check, all_authors
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                # Ignore errors in author collection
                pass
        
        # Log a brief summary and merge all_authors into env.git_last_updated
        if all_authors:
            uniq = set()
            for s in all_authors.values():
                uniq.update(s)
            logger.debug(
                'collected %d unique authors across %d files',
                len(uniq), len(all_authors)
            )
        # Merge all_authors into env.git_last_updated
        for docname, (src_dir, filename) in src_paths.items():
            timestamp, show_sourcelink, single_author = (
                env.git_last_updated[docname]
            )
            
            # Collect authors from source file and its dependencies
            authors_set = set()
            considered_files = []
            if filename in all_authors:
                authors_set.update(all_authors[filename])
                considered_files.append(filename)
            
            for dep_dir, dep_filename in dep_paths.get(docname, []):
                if dep_filename in all_authors:
                    authors_set.update(all_authors[dep_filename])
                    considered_files.append(dep_filename)
            
            # Replace single author with set of all authors
            env.git_last_updated[docname] = (
                timestamp, show_sourcelink, authors_set or {single_author}
                if single_author else set()
            )
            if considered_files:
                logger.debug(
                    'authors inputs for %s: %s',
                    docname, ', '.join(sorted(considered_files))
                )


def _html_page_context(app, pagename, templatename, context, doctree):
    context['last_updated'] = None
    lufmt = app.config.html_last_updated_fmt
    if lufmt is None or 'sourcename' not in context:
        return
    if 'page_source_suffix' not in context:
        # This happens in 'singlehtml' builders
        assert context['sourcename'] == ''
        return

    data = app.env.git_last_updated[pagename]
    if data is None:
        # There was a problem with git, a warning has already been issued
        timestamp = None
        show_sourcelink = False
        author = None
    else:
        timestamp, show_sourcelink, author = data
    if not show_sourcelink:
        del context['sourcename']
        del context['page_source_suffix']
    if timestamp is None:
        return

    utc_date = datetime.fromtimestamp(int(timestamp), timezone.utc)
    date = utc_date.astimezone(app.config.git_last_updated_timezone)
    
    # Format date according to user's preference if provided, otherwise use locale based on language
    datefmt = lufmt or set_locale_date_fmt(app)
    date_str = format_date(
        datefmt,
        date=date,
        language=app.config.language)

    if author and (app.config.git_show_author or app.config.git_show_all_authors):
        # Handle both single author (string) and multiple authors (set)
        if isinstance(author, set):
            # Format multiple authors: "edited by Author1, Author2, and Author3"
            authors_list = sorted(author)
            if len(authors_list) == 1:
                author_names = authors_list[0]
            elif len(authors_list) == 2:
                author_names = translate('%(author1)s and %(author2)s') % {
                    'author1': authors_list[0],
                    'author2': authors_list[1]
                }
            else:
                # Three or more authors: "Author1, Author2, and Author3"
                all_but_last = ', '.join(authors_list[:-1])
                author_names = translate(
                    '%(authors)s, and %(last_author)s') % {
                    'authors': all_but_last,
                    'last_author': authors_list[-1]
                }
            # Use "edited by" for all authors list
            author_str = translate('edited by %(author)s') % {
                'author': author_names
            }
            context['last_updated'] = date_str + ', ' + author_str
        else:
            # Single author (most recent): use "by" without comma
            author_str = translate('by %(author)s') % {'author': author}
            context['last_updated'] = date_str + ' ' + author_str
    else:
        context['last_updated'] = date_str

    if app.config.git_last_updated_metatags:
        context['metatags'] += """
    <meta property="article:modified_time" content="{}" />""".format(
            date.isoformat())


def _config_inited(app, config):
    if config.html_last_updated_fmt is None:
        config.html_last_updated_fmt = ''
    if isinstance(config.git_last_updated_timezone, str):
        from babel.dates import get_timezone
        config.git_last_updated_timezone = get_timezone(
            config.git_last_updated_timezone)


def _builder_inited(app):
    env = app.env
    if not hasattr(env, 'git_last_updated'):
        env.git_last_updated = {}


def _source_read(app, docname, source):
    env = app.env
    if docname not in env.found_docs:
        # Since Sphinx 7.2, "docname" can be None or a relative path
        # to a file included with the "include" directive.
        # We are only interested in actual source documents.
        return
    if docname in env.git_last_updated:
        # Again since Sphinx 7.2, the source-read hook can be called
        # multiple times when using the "include" directive.
        return
    env.git_last_updated[docname] = None


def _env_merge_info(app, env, docnames, other):
    env.git_last_updated.update(other.git_last_updated)


def _env_purge_doc(app, env, docname):
    try:
        del env.git_last_updated[docname]
    except KeyError:
        pass


def setup(app):
    """Sphinx extension entry point."""
    app.require_sphinx('1.8')  # For "config-inited" event
    app.connect('html-page-context', _html_page_context)
    app.connect('config-inited', _config_inited)
    app.connect('env-updated', _env_updated)
    app.connect('builder-inited', _builder_inited)
    app.connect('source-read', _source_read)
    app.connect('env-merge-info', _env_merge_info)
    app.connect('env-purge-doc', _env_purge_doc)
    app.add_config_value(
        'git_untracked_check_dependencies', True, rebuild='env')
    app.add_config_value(
        'git_untracked_show_sourcelink', False, rebuild='env')
    app.add_config_value(
        'git_last_updated_timezone', None, rebuild='env')
    app.add_config_value(
        'git_last_updated_metatags', True, rebuild='html')
    app.add_config_value(
        'git_show_author', False, rebuild='html')
    app.add_config_value(
        'git_show_all_authors', False, rebuild='env')
    # Register this extension's message catalog for i18n of convenience strings
    try:
        locale_dir = str((Path(__file__).parent / 'locale').resolve())
        app.add_message_catalog(MESSAGE_CATALOG_NAME, locale_dir)
    except Exception:
        # If unavailable at build time, fail gracefully;
        # strings will fall back to English
        pass
    app.add_config_value('git_exclude_patterns', [], rebuild='env')
    app.add_config_value(
        'git_exclude_commits', [], rebuild='env')
    app.add_config_value(
        'git_first_parent', False, rebuild='env')
    app.add_config_value(
        'git_show_merge_commits', False, rebuild='env')
    return {
        'version': __version__,
        'parallel_read_safe': True,
        'env_version': 1,
    }


def set_locale_date_fmt(app):
    lang = (app.config.language or "en").replace("-", "_").lower()
    fmt = FMT_BY_LANG.get(lang, FMT_BY_LANG.get(lang.split("_")[0], "%B %-d, %Y"))
    return fmt


# Map language codes to strftime patterns.
# Adjust/extend as needed for your locales.

FMT_BY_LANG = {
    # English family (month name first)
    "en": "%B %-d, %Y",
    "en_GB": "%-d %B %Y",

    # East Asian (year-first locales typically write D M Y for long text)
    "zh-cn": "%Y年%-m月%-d日",       # Chinese (Simplified)
    "zh-tw": "%Y年%-m月%-d日",       # Chinese (Traditional)
    "ja": "%Y年%-m月%-d日",          # Japanese
    "ko": "%Y년 %-m월 %-d일",        # Korean

    # South Asian
    "hi": "%-d %B %Y",              # Hindi
    "bn": "%-d %B %Y",              # Bengali
    "ta": "%-d %B %Y",              # Tamil

    # Southeast Asian
    "th": "%-d %B %Y",              # Thai (B.E. calendars exist, but Sphinx uses Python's G.E. year)
    "vi": "%-d %B, %Y",             # Vietnamese
    "id": "%-d %B %Y",              # Indonesian
    "ms": "%-d %B %Y",              # Malay

    # European Romance & Germanic
    "es": "%-d de %B de %Y",        # Spanish
    "fr": "%-d %B %Y",              # French
    "pt": "%-d de %B de %Y",        # Portuguese
    "it": "%-d %B %Y",              # Italian
    "ro": "%-d %B %Y",              # Romanian
    "nl": "%-d %B %Y",              # Dutch
    "de": "%-d. %B %Y",             # German (note the dot after day)
    "sv": "%-d %B %Y",              # Swedish
    "no": "%-d. %B %Y",             # Norwegian (bokmål form commonly uses a dot)
    "cs": "%-d. %B %Y",             # Czech
    "hu": "%Y. %B %-d.",            # Hungarian (year. month day.)
    "pl": "%-d %B %Y",              # Polish

    # Hellenic & Slavic
    "el": "%-d %B %Y",              # Greek
    "ru": "%-d %B %Y г.",           # Russian (“г.” after year)
    "uk": "%-d %B %Y р.",           # Ukrainian (“р.” after year)
    "tr": "%-d %B %Y",              # Turkish

    # Middle Eastern / RTL
    "ar": "%-d %B %Y",              # Arabic
    "fa": "%-d %B %Y",              # Persian
    "he": "%-d ב%B %Y",             # Hebrew (preposition “ב” before month)

    # Extras from your list
    "sw": "%-d %B %Y",              # Swahili
}

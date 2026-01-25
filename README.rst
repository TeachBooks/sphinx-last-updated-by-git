Get the "last updated" time for each Sphinx page from Git
=========================================================

This is a little Sphinx_ extension that does exactly that.
It also checks for included files and other dependencies and
uses their "last updated" time if it's more recent.
For each file, the "author date" of the Git commit where it was last changed
is taken to be its "last updated" time.  Uncommitted changes are ignored.

If a page doesn't have a source file, its last_updated_ time is set to ``None``.

The default value for html_last_updated_fmt_ is changed
from ``None`` to the empty string.

If html_last_updated_fmt_ is set to the empty string,
the date is displayed in a localized format based on the value of language_.

Usage
    #. Make sure that you use a Sphinx theme that shows the "last updated"
       information (or use a custom template with last_updated_)
    #. Install the Python package ``sphinx-last-updated-by-git``
    #. Add ``'sphinx_last_updated_by_git'`` to ``extensions`` in your ``conf.py``
    #. Run Sphinx!

Options
    * If a source file is not tracked by Git (e.g. because it has been
      auto-generated on demand by autosummary_generate_ or has not been committed to Git) but its dependencies
      are, the last_updated_ time is taken from them.  If you don't want this
      to happen, use ``git_untracked_check_dependencies = False``.

    * If a source file is not tracked by Git (e.g. because it has been
      auto-generated on demand by autosummary_generate_ or has not been committed to Git), 
      by default its HTML page will not show a last updated timestamp.
      Source links are still included if ``html_copy_source_`` and
      ``html_show_sourcelink_`` are ``True`` (and
      the theme you are using must support source links in the first place).
      If you don't want this behavior, set ``git_untracked_show_sourcelink = False``.
      In this case, its HTML page also doesn't get a source link.

    * By default, timestamps are displayed using the local time zone.
      You can specify a datetime.timezone_ object (or any ``tzinfo`` subclass
      instance) with the configuration option ``git_last_updated_timezone``.
      You can also use any string recognized by babel_,
      e.g. ``git_last_updated_timezone = 'Pacific/Auckland'``.

    * By default, the "last updated" timestamp is added as an HTML ``<meta>``
      tag.  This can be disabled by setting the configuration option
      ``git_last_updated_metatags`` to ``False``.

    * By default, only the date is shown in the "last updated" information.
      You can also show the author of the last commit by setting
      ``git_show_author = True`` in your ``conf.py``.
      This will append "by <author name>" to the date string.
      The author name is taken from the Git commit's author field (``%aN``).

    * To show ALL authors who have edited a file (not just the most recent),
      set ``git_show_all_authors = True`` in your ``conf.py``.
      This will display "<date>, edited by <author1>, <author2>, and <author3>"
      instead of just "<date> by <last author>".
      Note: This performs an additional Git pass to collect all unique authors
      (only when this option is enabled), which may slightly increase build
      time for large repositories. Author names are sorted alphabetically.
      You can also combine this with ``git_show_author = True`` if desired,
      but ``git_show_all_authors = True`` alone is sufficient to display authors.
      Co-authors listed in commit message trailers (``Co-authored-by:``) are
      also included automatically; emails are stripped and only names are shown.

    * Author aliases: if your Git history has usernames and you want to show
      real names (or any custom label) in the footer, set
      ``git_author_aliases`` to a mapping in ``conf.py``, e.g.::

        git_author_aliases = {
          'johndoe': 'John Doe',
          'alice': 'Alice Smith',
        }

      If an alias is defined, the displayed author name is replaced with the
      mapped value. If multiple usernames map to the same display name, the
      output is deduplicated. Aliases are matched after trimming whitespace,
      and if an exact key is not found, a lowercase key is also tried.

    * Files can be excluded from the last updated date calculation by passing
      a list of exclusion patterns to the configuration option
      ``git_exclude_patterns``.
      These patterns are checked on both source files and dependencies
      and are treated the same way as Sphinx's exclude_patterns_.

    * Individual commits can be excluded from the last updated date
      calculation by passing a list of commit hashes to the configuration
      option ``git_exclude_commits``.

    * By default, the last updated date comes from the commit where the content
      was authored, even if that was on a merged branch (timestamps of merge commits themselves are not taken into account).
      If instead you want to see the date of when that change was merged into
      the current branch (following only the first-parent path), set
      ``git_last_updated_when_merged = True`` in your ``conf.py``.

Caveats
    * When using a "Git shallow clone" (with the ``--depth`` option),
      the "last updated" commit for a long-unchanged file
      might not have been checked out.
      In this case, the last_updated_ time is set to ``None``
      (and a warning is shown during the build).

      This might happen on https://readthedocs.org/
      because they use shallow clones by default.
      To avoid this problem, you can edit your config file ``.readthedocs.yml``:

      .. code:: yaml

          version: 2
          build:
            os: "ubuntu-22.04"
            tools:
              python: "3"
            jobs:
              post_checkout:
                - git fetch --unshallow || true

      For more details, `read the docs`__.

      __ https://docs.readthedocs.com/platform/stable/build-customization.html#unshallow-git-clone

      This might also happen when using Github Actions,
      because `actions/checkout`__ also uses shallow clones by default.
      This can be changed by using ``fetch-depth: 0``:

      .. code:: yaml

          steps:
            - uses: actions/checkout@v3
              with:
                fetch-depth: 0

      __ https://github.com/actions/checkout

      If you only want to get rid of the warning (without actually fixing the problem),
      use this in your ``conf.py``::

          suppress_warnings = ['git.too_shallow']

    * If depedency file does not exist, a warning is being emitted.

      If you only want to get rid of the warning (without actually fixing the problem),
      use this in your ``conf.py``::

          suppress_warnings = ['git.dependency_not_found']

    * When a project on https://readthedocs.org/ using their default theme
      ``sphinx_rtd_theme`` was created before October 20th 2020,
      the date will not be displayed in the footer.

      An outdated work-around is to enable the (undocumented) feature flag
      ``USE_SPHINX_LATEST``.

      A better work-around is to override the defaults
      by means of a ``requirements.txt`` file containing something like this::

          sphinx>=2
          sphinx_rtd_theme>=0.5

      See also `issue #1`_.

    * In Sphinx versions 5.0 and 5.1, there has been
      a regression in how dependencies are determined.
      This could lead to spurious dependencies
      which means that some "last changed" dates were wrong.
      This has been fixed in Sphinx version 5.2 and above.

      See also `issue #40`_.

License
    BSD-2-Clause (same as Sphinx itself),
    for more information take a look at the ``LICENSE`` file.

Similar stuff
    | https://github.com/jdillard/sphinx-gitstamp
    | https://github.com/OddBloke/sphinx-git
    | https://github.com/MestreLion/git-tools (``git-restore-mtime``)
    | https://github.com/TYPO3-Documentation/sphinxcontrib-gitloginfo

.. _Sphinx: https://www.sphinx-doc.org/
.. _last_updated: https://www.sphinx-doc.org/en/master/
    development/html_themes/templating.html#last_updated
.. _exclude_patterns: https://www.sphinx-doc.org/en/master/usage/
    configuration.html#confval-exclude_patterns
.. _autosummary_generate: https://www.sphinx-doc.org/en/master/
    usage/extensions/autosummary.html#confval-autosummary_generate
.. _html_copy_source: https://www.sphinx-doc.org/en/master/
    usage/configuration.html#confval-html_copy_source
.. _html_show_sourcelink: https://www.sphinx-doc.org/en/master/
    usage/configuration.html#confval-html_show_sourcelink
.. _html_last_updated_fmt: https://www.sphinx-doc.org/en/master/
    usage/configuration.html#confval-html_last_updated_fmt
.. _datetime.timezone: https://docs.python.org/3/library/
    datetime.html#timezone-objects
.. _babel: https://babel.pocoo.org/
.. _issue #1: https://github.com/mgeier/sphinx-last-updated-by-git/issues/1
.. _issue #40: https://github.com/mgeier/sphinx-last-updated-by-git/issues/40

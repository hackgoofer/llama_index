from sphinx.application import Sphinx
from docutils import nodes
from sphinx.environment.adapters.toctree import TocTree
from sphinx.util.matching import Matcher

from typing import TYPE_CHECKING, Any, TypeVar

from docutils import nodes
from docutils.nodes import Element, Node

from sphinx import addnodes
from sphinx.locale import __
from sphinx.util import logging, url_re
from sphinx.util.matching import Matcher
from sphinx.util.nodes import clean_astext
from sphinx.util.tags import Tags

from collections.abc import Iterable, Set

from sphinx.builders import Builder
from sphinx.environment import BuildEnvironment
from sphinx.util.tags import Tags

# Most of the code is copied from https://github.com/sphinx-doc/sphinx/blob/3b5d2afb9a20f1bfa9a6828209fc10151905cea3/sphinx/environment/adapters/toctree.py#L8
# I will highlight the changes I made


def _only_node_keep_children(node: addnodes.only, tags: Tags) -> bool:
    """Keep children if tags match or error."""
    try:
        return tags.eval_condition(node['expr'])
    except Exception as err:
        logger.warning(
            __('exception while evaluating only directive expression: %s'),
            err,
            location=node)
        return True


def global_toctree_for_doc(
    env: BuildEnvironment,
    docname: str,
    builder: Builder,
    collapse: bool = False,
    includehidden: bool = True,
    maxdepth: int = 0,
    titles_only: bool = False,
) -> Element | None:
    """Get the global ToC tree at a given document.

    This gives the global ToC, with all ancestors and their siblings.
    """

    toctrees: list[Element] = []
    for toctree_node in env.master_doctree.findall(addnodes.toctree):
        if toctree := _resolve_toctree(
            env,
            docname,
            builder,
            toctree_node,
            prune=True,
            maxdepth=int(maxdepth),
            titles_only=titles_only,
            collapse=collapse,
            includehidden=includehidden,
        ):
            toctrees.append(toctree)
    if not toctrees:
        return None
    result = toctrees[0]
    for toctree in toctrees[1:]:
        result.extend(toctree.children)
    return result


def _resolve_toctree(
    env: BuildEnvironment, docname: str, builder: Builder, toctree: addnodes.toctree, *,
    prune: bool = True, maxdepth: int = 0, titles_only: bool = False,
    collapse: bool = False, includehidden: bool = False,
) -> Element | None:
    """Resolve a *toctree* node into individual bullet lists with titles
    as items, returning None (if no containing titles are found) or
    a new node.

    If *prune* is True, the tree is pruned to *maxdepth*, or if that is 0,
    to the value of the *maxdepth* option on the *toctree* node.
    If *titles_only* is True, only toplevel document titles will be in the
    resulting tree.
    If *collapse* is True, all branches not containing docname will
    be collapsed.
    """

    if toctree.get('hidden', False) and not includehidden:
        return None

    # For reading the following two helper function, it is useful to keep
    # in mind the node structure of a toctree (using HTML-like node names
    # for brevity):
    #
    # <ul>
    #   <li>
    #     <p><a></p>
    #     <p><a></p>
    #     ...
    #     <ul>
    #       ...
    #     </ul>
    #   </li>
    # </ul>
    #
    # The transformation is made in two passes in order to avoid
    # interactions between marking and pruning the tree (see bug #1046).

    toctree_ancestors = _get_toctree_ancestors(env.toctree_includes, docname)
    included = Matcher(env.config.include_patterns)
    excluded = Matcher(env.config.exclude_patterns)

    maxdepth = maxdepth or toctree.get('maxdepth', -1)
    if not titles_only and toctree.get('titlesonly', False):
        titles_only = True
    if not includehidden and toctree.get('includehidden', False):
        includehidden = True

    tocentries = _entries_from_toctree(
        env,
        prune,
        titles_only,
        collapse,
        includehidden,
        builder.tags,
        toctree_ancestors,
        included,
        excluded,
        toctree,
        [],
    )
    if not tocentries:
        return None

    newnode = addnodes.compact_paragraph('', '')
    if caption := toctree.attributes.get('caption'):
        caption_node = nodes.title(caption, '', *[nodes.Text(caption)])
        caption_node.line = toctree.line
        caption_node.source = toctree.source
        caption_node.rawsource = toctree['rawcaption']
        if hasattr(toctree, 'uid'):
            # move uid to caption_node to translate it
            caption_node.uid = toctree.uid  # type: ignore[attr-defined]
            del toctree.uid
        newnode.append(caption_node)
    newnode.extend(tocentries)
    newnode['toctree'] = True

    # prune the tree to maxdepth, also set toc depth and current classes
    _toctree_add_classes(newnode, 1, docname)
    newnode = _toctree_copy(newnode, 1, maxdepth if prune else 0, collapse, builder.tags)

    if isinstance(newnode[-1], nodes.Element) and len(newnode[-1]) == 0:  # No titles found
        return None

    # set the target paths in the toctrees (they are not known at TOC
    # generation time)
    for refnode in newnode.findall(nodes.reference):
        if url_re.match(refnode['refuri']) is None:
            rel_uri = builder.get_relative_uri(docname, refnode['refuri'])
            refnode['refuri'] = rel_uri + refnode['anchorname']
    return newnode


def _entries_from_toctree(
    env: BuildEnvironment,
    prune: bool,
    titles_only: bool,
    collapse: bool,
    includehidden: bool,
    tags: Tags,
    toctree_ancestors: Set[str],
    included: Matcher,
    excluded: Matcher,
    toctreenode: addnodes.toctree,
    parents: list[str],
    subtree: bool = False,
) -> list[Element]:
    """Return TOC entries for a toctree node."""
    entries: list[Element] = []
    for (title, ref) in toctreenode['entries']:
        try:
            toc, refdoc = _toctree_entry(
                title, ref, env, prune, collapse, tags, toctree_ancestors,
                included, excluded, toctreenode, parents,
            )
        except LookupError:
            continue

        # children of toc are:
        # - list_item + compact_paragraph + (reference and subtoc)
        # - only + subtoc
        # - toctree
        children: Iterable[nodes.Element] = toc.children  # type: ignore[assignment]

        # if titles_only is given, only keep the main title and
        # sub-toctrees
        if titles_only:
            # delete everything but the toplevel title(s)
            # and toctrees
            for top_level in children:
                # nodes with length 1 don't have any children anyway
                if len(top_level) > 1:
                    if subtrees := list(top_level.findall(addnodes.toctree)):
                        top_level[1][:] = subtrees  # type: ignore[index]
                    else:
                        top_level.pop(1)
        # resolve all sub-toctrees
        for sub_toc_node in list(toc.findall(addnodes.toctree)):
            if sub_toc_node.get('hidden', False) and not includehidden:
                continue
            for i, entry in enumerate(
                _entries_from_toctree(
                    env,
                    prune,
                    titles_only,
                    collapse,
                    includehidden,
                    tags,
                    toctree_ancestors,
                    included,
                    excluded,
                    sub_toc_node,
                    [refdoc] + parents,
                    subtree=True,
                ),
                start=sub_toc_node.parent.index(sub_toc_node) + 1,
            ):
                sub_toc_node.parent.insert(i, entry)
            sub_toc_node.parent.remove(sub_toc_node)

        entries.extend(children)

    if not subtree:
        ret = nodes.bullet_list()
        ret += entries
        return [ret]

    return entries


def _toctree_entry(
    title: str,
    ref: str,
    env: BuildEnvironment,
    prune: bool,
    collapse: bool,
    tags: Tags,
    toctree_ancestors: Set[str],
    included: Matcher,
    excluded: Matcher,
    toctreenode: addnodes.toctree,
    parents: list[str],
) -> tuple[Element, str]:
    from sphinx.domains.std import StandardDomain

    try:
        refdoc = ''
        if url_re.match(ref):
            toc = _toctree_url_entry(title, ref)
        elif ref == 'self':
            toc = _toctree_self_entry(title, toctreenode['parent'], env.titles)
        elif ref in StandardDomain._virtual_doc_names:
            toc = _toctree_generated_entry(title, ref)
        else:
            if ref in parents:
                logger.warning(__('circular toctree references '
                                  'detected, ignoring: %s <- %s'),
                               ref, ' <- '.join(parents),
                               location=ref, type='toc', subtype='circular')
                msg = 'circular reference'
                raise LookupError(msg)

            toc, refdoc = _toctree_standard_entry(
                title,
                ref,
                env.metadata[ref].get('tocdepth', 0),
                env.tocs[ref],
                toctree_ancestors,
                prune,
                collapse,
                tags,
            )

        if not toc.children:
            # empty toc means: no titles will show up in the toctree
            logger.warning(__('toctree contains reference to document %r that '
                              "doesn't have a title: no link will be generated"),
                           ref, location=toctreenode)
    except KeyError:
        # this is raised if the included file does not exist
        ref_path = env.doc2path(ref, False)
        if excluded(ref_path):
            message = __('toctree contains reference to excluded document %r')
        elif not included(ref_path):
            message = __('toctree contains reference to non-included document %r')
        else:
            message = __('toctree contains reference to nonexisting document %r')

        logger.warning(message, ref, location=toctreenode)
        raise
    return toc, refdoc


def _toctree_add_classes(node: Element, depth: int, docname: str) -> None:
    """Add 'toctree-l%d' and 'current' classes to the toctree."""
    for subnode in node.children:
        if isinstance(subnode, (addnodes.compact_paragraph, nodes.list_item)):
            # for <p> and <li>, indicate the depth level and recurse
            subnode['classes'].append(f'toctree-l{depth - 1}')
            _toctree_add_classes(subnode, depth, docname)
        elif isinstance(subnode, nodes.bullet_list):
            # for <ul>, just recurse
            _toctree_add_classes(subnode, depth + 1, docname)
        elif isinstance(subnode, nodes.reference):
            # for <a>, identify which entries point to the current
            # document and therefore may not be collapsed
            if subnode['refuri'] == docname:
                if not subnode['anchorname']:
                    # give the whole branch a 'current' class
                    # (useful for styling it differently)
                    branchnode: Element = subnode
                    while branchnode:
                        branchnode['classes'].append('current')
                        branchnode = branchnode.parent
                # mark the list_item as "on current page"
                if subnode.parent.parent.get('iscurrent'):
                    # but only if it's not already done
                    return
                while subnode:
                    subnode['iscurrent'] = True
                    subnode = subnode.parent

ET = TypeVar('ET', bound=Element)
def _toctree_copy(node: ET, depth: int, maxdepth: int, collapse: bool, tags: Tags) -> ET:
    """Utility: Cut and deep-copy a TOC at a specified depth."""
    keep_bullet_list_sub_nodes = (depth <= 1
                                  or ((depth <= maxdepth or maxdepth <= 0)
                                      and (not collapse or 'iscurrent' in node)))

    copy = node.copy()
    for subnode in node.children:
        if isinstance(subnode, (addnodes.compact_paragraph, nodes.list_item)):
            # for <p> and <li>, just recurse
            copy.append(_toctree_copy(subnode, depth, maxdepth, collapse, tags))
        elif isinstance(subnode, nodes.bullet_list):
            # for <ul>, copy if the entry is top-level
            # or, copy if the depth is within bounds and;
            # collapsing is disabled or the sub-entry's parent is 'current'.
            # The boolean is constant so is calculated outwith the loop.
            if keep_bullet_list_sub_nodes:
                copy.append(_toctree_copy(subnode, depth + 1, maxdepth, collapse, tags))
        elif isinstance(subnode, addnodes.toctree):
            # copy sub toctree nodes for later processing
            copy.append(subnode.copy())
        elif isinstance(subnode, addnodes.only):
            # only keep children if the only node matches the tags
            if _only_node_keep_children(subnode, tags):
                for child in subnode.children:
                    copy.append(_toctree_copy(
                        child, depth, maxdepth, collapse, tags,  # type: ignore[type-var]
                    ))
        elif isinstance(subnode, (nodes.reference, nodes.title)):
            # deep copy references and captions
            sub_node_copy = subnode.copy()
            sub_node_copy.children = [child.deepcopy() for child in subnode.children]
            for child in sub_node_copy.children:
                child.parent = sub_node_copy
            copy.append(sub_node_copy)
        else:
            msg = f'Unexpected node type {subnode.__class__.__name__!r}!'
            raise ValueError(msg)
    return copy


def _get_toctree_ancestors(
    toctree_includes: dict[str, list[str]], docname: str,
) -> Set[str]:
    parent: dict[str, str] = {}
    for p, children in toctree_includes.items():
        parent |= dict.fromkeys(children, p)
    ancestors: list[str] = []
    d = docname
    while d in parent and d not in ancestors:
        ancestors.append(d)
        d = parent[d]
    # use dict keys for ordered set operations
    return dict.fromkeys(ancestors).keys()


def resolve_sidebar(app: Sphinx, n_doctree, n_docname):
    if n_docname == 'index':
        # Your code to handle the index.rst file
        toctree = TocTree(app.env)
        print("toctree")
        print(toctree)
        ancestors = toctree.get_toctree_ancestors(n_docname)
        print("ancestors")
        print(ancestors)
        node = global_toctree_for_doc(app.env, n_docname, app.builder, collapse=False)
        print("node")
        print(node)

def setup(app):
    app.connect("doctree-resolved", resolve_sidebar)
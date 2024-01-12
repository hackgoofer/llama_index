from sphinx.application import Sphinx
from docutils import nodes
from sphinx.environment.adapters.toctree import TocTree

def resolve_sidebar(app: Sphinx, n_doctree, n_docname):
    if n_docname == 'index':
        # Your code to handle the index.rst file
        toctree = TocTree(app.env)
        print("toctree")
        print(toctree)
        ancestors = toctree.get_toctree_ancestors(n_docname)
        print("ancestors")
        print(ancestors)
        node = toctree.get_toctree_for(n_docname, app.builder, collapse=False)
        print("node")
        print(node)

def setup(app):
    app.connect("doctree-resolved", resolve_sidebar)
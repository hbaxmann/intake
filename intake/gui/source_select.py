#-----------------------------------------------------------------------------
# Copyright (c) 2012 - 2019, Anaconda, Inc. and Intake contributors
# All rights reserved.
#
# The full license is in the LICENSE file, distributed with this software.
#-----------------------------------------------------------------------------

from collections import OrderedDict

import intake
import panel as pn

from .base import Base

def coerce_to_list(items, preprocess=None):
    """Given an instance or list, coerce to list.

    With optional preprocessing.
    """
    if not isinstance(items, list):
        items = [items]
    if preprocess:
        items = list(map(preprocess, items))
    return items


class BaseSelector(Base):
    """Base class for capturing selector logic.

    Parameters
    ----------
    preprocess: function
        run on every input value when creating options
    widget: panel widget
        selector widget which this class keeps uptodate with class properties
    """
    preprocess = None
    widget = None


    @property
    def labels(self):
        """Labels of items in widget"""
        return self.widget.labels

    @property
    def items(self):
        """Available items to select from"""
        return self.widget.values

    @items.setter
    def items(self, items):
        """When setting items make sure widget options are uptodate"""
        if items is not None:
            self.options = items

    def _create_options(self, items):
        """Helper method to create options from list, or instance.

        Applies preprocess method if available to create a uniform
        output
        """
        return OrderedDict(map(lambda x: (x.name, x),
                           coerce_to_list(items, self.preprocess)))

    @property
    def options(self):
        """Options available on the widget"""
        return self.widget.options

    @options.setter
    def options(self, new):
        """Set options from list, or instance of named item

        Over-writes old options
        """
        options = self._create_options(new)
        if self.widget.value:
            self.widget.set_param(options=options, value=list(options.values())[:1])
        else:
            self.widget.options = options
            self.widget.value = list(options.values())[:1]

    def add(self, items):
        """Add items to options"""
        options = self._create_options(items)
        for k, v in options.items():
            if k in self.labels and v not in self.items:
                options.pop(k)
                count = 0
                while f'{k}_{count}' in self.labels:
                    count += 1
                options[f'{k}_{count}'] = v
        self.widget.options.update(options)
        self.widget.param.trigger('options')
        self.widget.value = list(options.values())[:1]

    def remove(self, items):
        """Remove items from options"""
        items = coerce_to_list(items)
        new_options = {k: v for k, v in self.options.items() if v not in items}
        self.widget.options = new_options
        self.widget.param.trigger('options')

    @property
    def selected(self):
        """Value sepected on the widget"""
        return self.widget.value

    @selected.setter
    def selected(self, new):
        """Set selected from list or instance of object or name.

        Over-writes existing selection
        """
        def preprocess(item):
            if isinstance(item, str):
                return self.options[item]
            return item
        items = coerce_to_list(new, preprocess)
        self.widget.value = items


class CatSelector(BaseSelector):
    """
    The cat selector takes a variety of inputs such as a catalog instance,
    a path to a catalog, or a list of either of those.

    Once the cat selector is populated with these options, the user can
    select which catalog(s) are of interest. These catalogs are stored on
    the ``selected`` property of the class.

    Parameters
    ----------
    cats: list of catalogs, opt
        catalogs used to initalize, can be provided as objects or
        urls pointing to local or remote catalogs.
    done_callback: func, opt
        called when the object's main job has completed. In this case,
        selecting catalog(s).

    Attributes
    ----------
    selected: list of cats
        list of selected cats
    items: list of cats
        list of all the catalog values represented in widget
    labels: list of str
        list of labels for all the catalog represented in widget
    options: dict
        dict of widget labels and values (same as `dict(zip(self.labels, self.values))`)
    children: list of panel objects
        children that will be used to populate the panel when visible
    panel: panel layout object
        instance of a panel layout (row or column) that contains children
        when visible
    watchers: list of param watchers
        watchers that are set on children - cleaned up when visible
        is set to false.
    """
    children = []

    def __init__(self, cats=None, done_callback=None, **kwargs):
        """Set cats to initialize the class.

        The order of the calls in this method matters and is different
        from the order in other panel init methods because the top level
        gui class needs to be able to watch these widgets.
        """
        self.panel = pn.Column(name='Select Catalog', margin=0)
        self.widget = pn.widgets.MultiSelect(size=9, min_width=200, width_policy='min')
        self.done_callback = done_callback
        super().__init__(**kwargs)

        self.items = cats if cats is not None else [intake.cat]

    def setup(self):
        self.remove_button = pn.widgets.Button(
            name='Remove Selected Catalog',
            width=200)

        self.watchers = [
            self.remove_button.param.watch(self.remove_selected, 'clicks'),
            self.widget.param.watch(self.expand_nested, 'value'),
            self.widget.param.watch(self.callback, 'value'),
        ]

        self.children = ['#### Catalogs', self.widget, self.remove_button]

    def callback(self, event):
        self.remove_button.disabled = not event.new
        if self.done_callback:
            self.done_callback(event.new)

    def preprocess(self, cat):
        """Function to run on each cat input"""
        if isinstance(cat, str):
            cat = intake.open_catalog(cat)
        return cat

    def expand_nested(self, event):
        """Populate widget with nested catalogs"""
        down = '│'
        right = '└──'

        def get_children(parent):
            return [e() for e in parent._entries.values() if e._container == 'catalog']

        if len(event.new) == 0:
            return

        got = event.new[0]
        obj = event.obj
        old = list(event.obj.options.items())
        name = next(k for k, v in old if v == got)
        index = next(i for i, (k, v) in enumerate(old) if v == got)
        if right in name:
            prefix = f'{name.split(right)[0]}{down} {right}'
        else:
            prefix = right

        children = get_children(got)
        for i, child in enumerate(children):
            old.insert(index+i+1, (f'{prefix} {child.name}', child))
        event.obj.options = dict(old)

    def collapse_nested(self, cats, max_nestedness=10):
        """
        Collapse any items that are nested under cats.
        `max_nestedness` acts as a fail-safe to prevent infinite looping.
        """
        children = []
        removed = set()
        nestedness = max_nestedness

        old = list(self.widget.options.values())
        nested = [cat for cat in old if getattr(cat, 'cat') is not None]
        parents = {cat.cat for cat in nested}
        parents_to_remove = cats
        while len(parents_to_remove) > 0 and nestedness > 0:
            for cat in nested:
                if cat.cat in parents_to_remove:
                    children.append(cat)
            removed = removed.union(parents_to_remove)
            nested = [cat for cat in nested if cat not in children]
            parents_to_remove = {c for c in children if c in parents - removed}
            nestedness -= 1
        self.remove(children)

    def remove_selected(self, *args):
        """Remove the selected catalog - allow the passing of arbitrary
        args so that buttons work. Also remove any nested catalogs."""
        self.collapse_nested(self.selected)
        self.remove(self.selected)


class SourceSelector(BaseSelector):
    """
    The source selector takes a variety of inputs such as cats or sources
    and uses those to populate a select widget containing all the sources.

    Once the source selector is populated with these options, the user can
    select which source(s) are of interest. These sources are stored on
    the ``selected`` property of the class.

    Parameters
    ----------
    cats: list of catalogs, opt
        catalogs used to initalize, provided as objects.
    sources: list of sources, opt
        sources used to initalize, provided as objects.
    done_callback: func, opt
        called when the object's main job has completed. In this case,
        selecting source(s).

    Attributes
    ----------
    selected: list of sources
        list of selected sources
    items: list of sources
        list of all the source values represented in widget
    labels: list of str
        list of labels for all the sources represented in widget
    options: dict
        dict of widget labels and values (same as `dict(zip(self.labels, self.values))`)
    children: list of panel objects
        children that will be used to populate the panel when visible
    panel: panel layout object
        instance of a panel layout (row or column) that contains children
        when visible
    watchers: list of param watchers
        watchers that are set on children - cleaned up when visible
        is set to false.
    """
    preprocess = None
    children = []

    def __init__(self, sources=None, cats=None, done_callback=None, **kwargs):
        """Set sources or cats to initialize the class - sources trumps cats.

        The order of the calls in this method matters and is different
        from the order in other panel init methods because the top level
        gui class needs to be able to watch these widgets.
        """
        self.panel = pn.Column(name='Select Data Source', margin=0)
        self.widget = pn.widgets.MultiSelect(size=9, min_width=200, width_policy='min')
        self.done_callback = done_callback
        super().__init__(**kwargs)

        if sources is not None:
            self.items = sources
        elif cats is not None:
            self.cats = cats

    def setup(self):
        self.watchers = [
            self.widget.param.watch(self.callback, 'value'),
        ]
        self.children = ['#### Entries', self.widget]

    @property
    def cats(self):
        """Cats represented in the sources options"""
        return set(source._catalog for source in self.items)

    @cats.setter
    def cats(self, cats):
        """Set sources from a list of cats"""
        sources = []
        for cat in coerce_to_list(cats):
            sources.extend([entry for entry in cat._entries.values() if entry._container != 'catalog'])
        self.items = sources

    def callback(self, event):
        if self.done_callback:
            self.done_callback(event.new)
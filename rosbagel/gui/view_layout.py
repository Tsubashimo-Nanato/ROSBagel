from __future__ import annotations

from typing import Any


class ViewLayout:
    """A splitter tree: each split changes only the selected view's rectangle."""

    def __init__(self, QtWidgets: Any, QtCore: Any, container: Any) -> None:
        self.QtWidgets = QtWidgets
        self.QtCore = QtCore
        self.layout = QtWidgets.QVBoxLayout(container)
        self.layout.setContentsMargins(6, 6, 6, 6)
        self.layout.setSpacing(0)

    def add(self, pane: Any) -> None:
        self.layout.addWidget(pane)

    def split(self, target: Any, pane: Any, direction: str, *, before: bool = False) -> None:
        orientation = (self.QtCore.Qt.Orientation.Horizontal if direction == "horizontal"
                       else self.QtCore.Qt.Orientation.Vertical)
        parent = target.parentWidget()
        splitter = self.QtWidgets.QSplitter(orientation)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        if isinstance(parent, self.QtWidgets.QSplitter):
            sizes = parent.sizes()
            parent.replaceWidget(parent.indexOf(target), splitter)
            # PySide's replaceWidget does not transfer Python ownership.
            splitter.setParent(parent)
            parent.setSizes(sizes)
        else:
            self.layout.replaceWidget(target, splitter)
        for widget in ([pane, target] if before else [target, pane]):
            splitter.addWidget(widget)
        splitter.setSizes([1, 1])
        splitter.show()
        pane.show()
        target.show()

    def remove(self, pane: Any) -> None:
        parent = pane.parentWidget()
        if not isinstance(parent, self.QtWidgets.QSplitter):
            self.layout.removeWidget(pane)
            pane.setParent(None)
            return
        pane.setParent(None)
        if parent.count() != 1:
            return
        survivor = parent.widget(0)
        grandparent = parent.parentWidget()
        survivor.setParent(None)
        if isinstance(grandparent, self.QtWidgets.QSplitter):
            sizes = grandparent.sizes()
            grandparent.replaceWidget(grandparent.indexOf(parent), survivor)
            survivor.setParent(grandparent)
            grandparent.setSizes(sizes)
        else:
            self.layout.replaceWidget(parent, survivor)
        parent.setParent(None)
        parent.deleteLater()
        survivor.show()

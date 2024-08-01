# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2007 Francois Aucamp <francois.aucamp@gmail.com>
# Copyright (C) 2024 Bardia Moshiri <fakeshell@bardia.tech>

"""Iterator with "value preview" capability."""

class PreviewIterator:
    """
    An ``iter`` wrapper class providing a "previewable" iterator.

    This "preview" functionality allows the iterator to return successive
    values from its ``iterable`` object, without actually moving forward
    itself. This is very usefuly if the next item(s) in an iterator must
    be used for something, after which the iterator should "undo" those
    read operations, so that they can be read again by another function.

    From the user point of view, this class supersedes the builtin iter()
    function: like iter(), it is called as PreviewIter(iterable).
    """
    def __init__(self, data):
        self._it = iter(data)
        self._cached_values = []
        self._preview_pos = 0

    def __iter__(self):
        return self

    def __next__(self):
        self.reset_preview()
        if len(self._cached_values) > 0:
            return self._cached_values.pop(0)
        return next(self._it)

    def preview(self):
        """
        Return the next item in the ``iteratable`` object

        But it does not modify the actual iterator (i.e. do not
        intefere with :func:`next`.

        Successive calls to :func:`preview` will return successive values from
        the ``iterable`` object, exactly in the same way :func:`next` does.

        However, :func:`preview` will always return the next item from
        ``iterable`` after the item returned by the previous :func:`preview`
        or :func:`next` call, whichever was called the most recently.
        To force the "preview() iterator" to synchronize with the "next()
        iterator" (without calling :func:`next`), use :func:`reset_preview`.
        """
        if self._preview_pos < len(self._cached_values):
            value = self._cached_values[self._preview_pos]
        else:
            value = next(self._it)
            self._cached_values.append(value)

        self._preview_pos += 1
        return value

    def reset_preview(self):
        self._preview_pos = 0

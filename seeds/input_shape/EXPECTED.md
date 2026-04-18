# input_shape seed

`compute_total` assumes object-shaped items but the legacy webhook hands it dicts.

## Expected exception

`AttributeError: 'dict' object has no attribute 'price'` at
`src/billing/summary.py::compute_total`.

## Expected winner

`input_shape` hypothesis — coerce dicts at the boundary (or raise a
clear domain error). The fix should accept both `LineItem` and `dict`
without the caller knowing which is which.

## Anti-fix

A `try/except AttributeError` that swallows the error is wrong. The
product contract is "summing works on legacy dicts"; the fix has to
actually sum them.

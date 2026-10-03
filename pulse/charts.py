"""Shared presentation range; never changes the underlying wealth series."""
import math

def wealth_range(values):
    finite = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not finite:
        return (0., 1.)
    low, high = min(finite), max(finite)
    # A minimum 5% span avoids magnifying tiny differences in a near-flat series.
    span = max(high-low, max(abs(low), abs(high))*0.05, 1.)
    padding = span*0.12
    center = (low+high)/2
    return (center-span/2-padding, center+span/2+padding)

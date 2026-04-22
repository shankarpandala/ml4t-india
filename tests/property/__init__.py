"""Hypothesis property-based tests for ml4t-india.

Every property test in this package asserts an invariant that must hold
for a class of inputs, not specific values. They complement the
example-based tests in ``tests/unit/`` by exploring the input space
more aggressively.

Keep property tests fast: Hypothesis' default ``max_examples=100`` is
plenty for the scale of invariants we care about, and CI lanes
(especially the free-threaded ones) are already slow enough without
property tests bumping it higher.
"""

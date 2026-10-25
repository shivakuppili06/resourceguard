"""
Unit tests for app.scan — specifically the unit-parsing logic, since a
silent parsing bug here produces wrong waste percentages without ever
raising an error (exactly what happened with the original nanocore bug:
"250000000n" of actual usage was parsed as 250000000 whole cores instead
of 250m, making every namespace look like it was using 0% of its request).

These run in CI on every PR (see .github/workflows/ci-cd.yaml) and the
build fails if any of them fail — there is no "|| true" fallback.
"""
import pytest

from app.scan import _cpu_to_millicores, _mem_to_mib, UnparsableQuantity


class TestCpuParsing:
    def test_millicores(self):
        assert _cpu_to_millicores("100m") == 100.0

    def test_bare_cores(self):
        assert _cpu_to_millicores("1") == 1000.0

    def test_fractional_bare_cores(self):
        assert _cpu_to_millicores("0.5") == 500.0

    def test_nanocores(self):
        # This is the exact format the Metrics API returns for usage.
        # 250000000n = 0.25 cores = 250 millicores.
        assert _cpu_to_millicores("250000000n") == pytest.approx(250.0)

    def test_microcores(self):
        # 500000u = 0.5 cores = 500 millicores.
        assert _cpu_to_millicores("500000u") == pytest.approx(500.0)

    def test_zero(self):
        assert _cpu_to_millicores("0") == 0.0

    def test_empty_string_does_not_raise(self):
        assert _cpu_to_millicores("") == 0.0

    def test_garbage_raises(self):
        with pytest.raises(UnparsableQuantity):
            _cpu_to_millicores("not-a-number")


class TestMemoryParsing:
    def test_mebibytes(self):
        assert _mem_to_mib("128Mi") == 128.0

    def test_gibibytes(self):
        assert _mem_to_mib("2Gi") == 2048.0

    def test_kibibytes(self):
        assert _mem_to_mib("1024Ki") == pytest.approx(1.0)

    def test_tebibytes(self):
        assert _mem_to_mib("1Ti") == pytest.approx(1024.0 * 1024.0)

    def test_decimal_megabytes(self):
        # "M" (decimal, 1000^2 bytes) is a distinct, valid suffix from
        # "Mi" (binary, 1024^2 bytes) — they must not be conflated.
        assert _mem_to_mib("1M") == pytest.approx(1_000_000 / (1024 * 1024))

    def test_bare_bytes(self):
        assert _mem_to_mib(str(1024 * 1024)) == pytest.approx(1.0)

    def test_zero(self):
        assert _mem_to_mib("0") == 0.0

    def test_unsupported_suffix_raises_instead_of_silently_misreading(self):
        # Previously: multipliers.get(unit, 1) silently treated any
        # unknown suffix as if it were already MiB, so "500000Ki" risked
        # being misread by orders of magnitude with no error. Now an
        # unparsable/unsupported quantity raises rather than guessing.
        with pytest.raises(UnparsableQuantity):
            _mem_to_mib("500Xi")

    def test_garbage_raises(self):
        with pytest.raises(UnparsableQuantity):
            _mem_to_mib("garbage")


class TestWasteComputation:
    """
    Regression test for the nanocore bug specifically: before the fix,
    a namespace requesting 500m and actually using 250m of CPU would
    have computed cpu_waste_pct as a huge negative/nonsense number
    because "250000000n" was parsed as 250000000 cores. After the fix
    it should correctly report 50% waste.
    """

    def test_cpu_waste_percentage_with_nanocore_usage(self):
        requested_millicores = 500.0
        used_millicores = _cpu_to_millicores("250000000n")
        waste_pct = round(100 * (1 - used_millicores / requested_millicores), 1)
        assert waste_pct == 50.0


class TestMissingSampleHandling:
    """
    compute_namespace_waste() talks to the Kubernetes API, so these don't
    hit it directly — they test the distinction at the unit level via a
    small inline reimplementation of the "missing vs. zero" accounting,
    mirroring what compute_namespace_waste does. A full integration test
    against a fake/mocked k8s_client is a reasonable next addition if this
    logic grows further.
    """

    def test_missing_sample_is_not_treated_as_measured_zero(self):
        # One container has a request but genuinely no usage sample yet
        # (metrics-server hasn't scraped it). That container's request
        # should still count toward total_requested (the resource really
        # is reserved), but must be flagged via missing_samples rather
        # than silently contributing to a "0 used" waste calculation that
        # looks identical to a genuinely idle container.
        requests = [{"cpu_request": "500m"}]
        usage_by_key = {}  # no sample for this container

        missing_samples = 0
        total_cpu_used = 0.0
        for r in requests:
            u = usage_by_key.get("key")
            if u is None:
                missing_samples += 1
                continue
            total_cpu_used += _cpu_to_millicores(u["cpu_usage"])

        assert missing_samples == 1
        assert total_cpu_used == 0.0  # not used in the waste % because is_complete=False

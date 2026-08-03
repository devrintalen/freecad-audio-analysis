"""Crossover filters.

Two things are worth testing hard here. The ladder synthesis is generated rather than
tabulated, so it is checked by loading each synthesised ladder with a pure resistor and
confirming it reproduces the alignment it was derived from. And the whole point of the
passive path is that a real driver is *not* a resistor, so that difference is tested too:
if a passive crossover behaved like its nominal alignment there would be no reason to
simulate one.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from freecad.audio_analysis.physics import air
from freecad.audio_analysis.physics.crossover import (
    ALIGNMENTS,
    Component,
    CrossoverError,
    IdealFilter,
    PassiveLadder,
    l_pad,
    ideal_transfer,
    ladder_prototype,
    make_filter,
    prototype_denominator,
    summing_response,
    synthesise,
)
from freecad.audio_analysis.physics.driver import DriverParameters
from freecad.audio_analysis.physics.network import GROUND, Compliance, Driver, Network


def db(values) -> np.ndarray:
    return 20.0 * np.log10(np.abs(values))


def thevenin(filter_, omega, source_impedance=0.0):
    """The open-circuit voltage gain and output impedance, for tests that want them.

    Deliberately *not* part of the Filter interface: this division is the one that
    diverges at a lossless ladder's own resonance. It is fine here because these tests
    stay away from that frequency, and it keeps them readable.
    """
    gain, alpha, beta = filter_.terminal_coefficients(omega, source_impedance)
    return gain / alpha, beta / alpha


def loaded_input_impedance(filter_, load, omega):
    """What the amplifier sees with a resistive ``load`` on the filter's output."""
    gain, alpha, beta = filter_.terminal_coefficients(omega, 0.0)
    current = gain / (alpha * load + beta)
    return filter_.amplifier_impedance(load * current, current, omega)


# ---------------------------------------------------------------------------------
# Prototypes
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize("alignment", ALIGNMENTS)
@pytest.mark.parametrize("order", [2, 4])
def test_prototype_is_three_db_down_at_the_corner(alignment, order):
    """Every alignment is normalised to the same -3 dB point.

    Otherwise changing alignment would silently move the crossover frequency, which would
    make comparing two designs meaningless.
    """
    if alignment == "Linkwitz-Riley":
        pytest.skip("Linkwitz-Riley is deliberately -6 dB at the corner")
    at_corner = ideal_transfer(alignment, order, "Lowpass", np.array([1j]))
    assert db(at_corner)[0] == pytest.approx(-3.0103, abs=0.02)


@pytest.mark.parametrize("order", [2, 4, 8])
def test_linkwitz_riley_is_six_db_down_at_the_corner(order):
    """The defining property: each branch is halved in amplitude so the pair sums flat."""
    at_corner = ideal_transfer("Linkwitz-Riley", order, "Lowpass", np.array([1j]))
    assert db(at_corner)[0] == pytest.approx(-6.0206, abs=0.02)


def test_linkwitz_riley_branches_sum_flat():
    """Low-pass plus high-pass is unity magnitude at every frequency, for even orders.

    This is why Linkwitz-Riley is the default for a multi-way system. At LR4 the branches
    are also in phase, so the sum is flat with no polarity trick; at LR2 they are 180
    degrees apart and one driver must be inverted, which is exactly the kind of thing the
    workbench should not leave the user to discover by ear.
    """
    s = 1j * np.logspace(-2, 2, 200)
    low = ideal_transfer("Linkwitz-Riley", 4, "Lowpass", s)
    high = ideal_transfer("Linkwitz-Riley", 4, "Highpass", s)
    assert np.allclose(np.abs(low + high), 1.0, atol=1e-9)


def test_linkwitz_riley_second_order_needs_a_polarity_flip():
    s = 1j * np.logspace(-2, 2, 200)
    low = ideal_transfer("Linkwitz-Riley", 2, "Lowpass", s)
    high = ideal_transfer("Linkwitz-Riley", 2, "Highpass", s)
    assert np.abs(low + high)[100] < 0.1  # cancels
    assert np.allclose(np.abs(low - high), 1.0, atol=1e-9)  # flat once inverted


def test_butterworth_odd_orders_sum_flat_in_magnitude():
    """Odd-order Butterworth is all-pass when summed: flat magnitude, rotating phase."""
    s = 1j * np.logspace(-2, 2, 200)
    total = ideal_transfer("Butterworth", 3, "Lowpass", s) + ideal_transfer(
        "Butterworth", 3, "Highpass", s
    )
    assert np.allclose(np.abs(total), 1.0, atol=1e-9)


@pytest.mark.parametrize("order", [1, 2, 3, 4])
def test_lowpass_rolls_off_six_db_per_octave_per_order(order):
    s = 1j * np.array([10.0, 20.0])
    values = db(ideal_transfer("Butterworth", order, "Lowpass", s))
    assert values[0] - values[1] == pytest.approx(6.0206 * order, abs=0.05)


@pytest.mark.parametrize("order", [1, 2, 3, 4])
def test_highpass_rolls_off_the_same_way_downward(order):
    s = 1j * np.array([0.1, 0.05])
    values = db(ideal_transfer("Butterworth", order, "Highpass", s))
    assert values[0] - values[1] == pytest.approx(6.0206 * order, abs=0.05)


def test_bypass_is_unity():
    s = 1j * np.logspace(-2, 2, 20)
    assert np.allclose(ideal_transfer("Butterworth", 2, "Bypass", s), 1.0)


def test_linkwitz_riley_rejects_odd_orders():
    with pytest.raises(CrossoverError, match="even orders"):
        prototype_denominator("Linkwitz-Riley", 3)


def test_unknown_alignment_is_rejected():
    with pytest.raises(CrossoverError, match="unknown alignment"):
        prototype_denominator("Chebyshev", 2)


def test_order_is_bounded():
    with pytest.raises(CrossoverError, match="order must be"):
        prototype_denominator("Butterworth", 99)


# ---------------------------------------------------------------------------------
# Ladder synthesis
# ---------------------------------------------------------------------------------


def test_prototype_matches_published_butterworth_values():
    """A spot check against the singly-terminated values in the filter literature.

    Included because a transcription error and a synthesis error look identical from
    inside the code; this pins the result to a number from outside it.
    """
    assert ladder_prototype("Butterworth", 2) == pytest.approx([1.4142, 0.7071], abs=1e-3)
    assert ladder_prototype("Butterworth", 3) == pytest.approx([1.5, 1.3333, 0.5], abs=1e-3)
    assert ladder_prototype("Butterworth", 4) == pytest.approx(
        [1.5307, 1.5772, 1.0824, 0.3827], abs=1e-3
    )


def test_linkwitz_riley_second_order_prototype():
    assert ladder_prototype("Linkwitz-Riley", 2) == pytest.approx([2.0, 0.5], abs=1e-6)


def loaded_response(components, load: float, omega: np.ndarray) -> np.ndarray:
    """Voltage across ``load`` when the ladder is driven from an ideal source."""
    ladder = PassiveLadder(list(components))
    A, B, _, _ = ladder.abcd(omega)
    # V_in = A V_out + B I_out, and I_out = V_out / load.
    return 1.0 / (A + B / load)


@pytest.mark.parametrize("alignment", ["Butterworth", "Bessel"])
@pytest.mark.parametrize("order", [1, 2, 3, 4])
@pytest.mark.parametrize("response", ["Lowpass", "Highpass"])
def test_synthesised_ladder_reproduces_its_alignment_into_a_resistor(
    alignment, order, response
):
    """The synthesis is correct if the ladder, loaded by the resistance it was designed
    for, gives back the transfer function it was derived from."""
    load, corner = 8.0, 1000.0
    frequency = np.logspace(1, 5, 300)
    omega = 2.0 * math.pi * frequency

    components = synthesise(alignment, order, response, corner, load)
    measured = loaded_response(components, load, omega)
    expected = ideal_transfer(alignment, order, response, 1j * frequency / corner)
    assert np.allclose(measured, expected, rtol=1e-6, atol=1e-9)


@pytest.mark.parametrize("order", [2, 4])
@pytest.mark.parametrize("response", ["Lowpass", "Highpass"])
def test_linkwitz_riley_ladder_reproduces_its_alignment(order, response):
    load, corner = 32.0, 2500.0
    frequency = np.logspace(1, 5, 300)
    omega = 2.0 * math.pi * frequency

    components = synthesise("Linkwitz-Riley", order, response, corner, load)
    measured = loaded_response(components, load, omega)
    expected = ideal_transfer("Linkwitz-Riley", order, response, 1j * frequency / corner)
    assert np.allclose(measured, expected, rtol=1e-6, atol=1e-9)


def test_lowpass_ladder_starts_with_a_series_inductor():
    components = synthesise("Butterworth", 2, "Lowpass", 1000.0, 8.0)
    assert [(c.kind, c.placement) for c in components] == [("L", "series"), ("C", "shunt")]


def test_highpass_ladder_is_the_dual():
    components = synthesise("Butterworth", 2, "Highpass", 1000.0, 8.0)
    assert [(c.kind, c.placement) for c in components] == [("C", "series"), ("L", "shunt")]


def test_first_order_lowpass_is_a_single_inductor():
    components = synthesise("Butterworth", 1, "Lowpass", 1000.0, 8.0)
    assert len(components) == 1
    assert components[0].kind == "L"
    # L = R / omega_c is the textbook value.
    assert components[0].value == pytest.approx(8.0 / (2 * math.pi * 1000.0))


def test_bypass_synthesises_nothing():
    assert synthesise("Butterworth", 2, "Bypass", 1000.0, 8.0) == []


def test_synthesis_rejects_nonsense_values():
    with pytest.raises(CrossoverError, match="frequency must be positive"):
        synthesise("Butterworth", 2, "Lowpass", 0.0, 8.0)
    with pytest.raises(CrossoverError, match="impedance must be positive"):
        synthesise("Butterworth", 2, "Lowpass", 1000.0, -1.0)


# ---------------------------------------------------------------------------------
# L-pad
# ---------------------------------------------------------------------------------


def test_l_pad_attenuates_by_the_requested_amount():
    load = 8.0
    omega = 2.0 * math.pi * np.array([100.0, 1000.0, 10000.0])
    measured = loaded_response(l_pad(-6.0, load), load, omega)
    assert db(measured) == pytest.approx([-6.0] * 3, abs=1e-9)


def test_l_pad_holds_the_filter_side_impedance_constant():
    """The reason to use two resistors instead of one: a bare series resistor would move
    the crossover point as well as the level."""
    load = 8.0
    omega = np.array([2.0 * math.pi * 1000.0])
    seen = loaded_input_impedance(PassiveLadder(l_pad(-10.0, load)), load, omega)
    assert seen[0].real == pytest.approx(load)
    assert seen[0].imag == pytest.approx(0.0)


def test_l_pad_is_empty_for_gain():
    assert l_pad(0.0, 8.0) == []
    assert l_pad(3.0, 8.0) == []  # a passive network cannot amplify


# ---------------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------------


def test_ideal_filter_applies_gain_and_delay():
    omega = np.array([2.0 * math.pi * 1000.0])
    filter_ = IdealFilter("Bypass", gain_db=-6.0, delay=250e-6)
    gain, back = thevenin(filter_, omega)
    assert db(gain)[0] == pytest.approx(-6.0)
    # 250 us at 1 kHz is a quarter period, so a quarter turn of phase.
    assert np.angle(gain)[0] == pytest.approx(-math.pi / 2, abs=1e-9)
    assert back[0] == 0.0


def test_ideal_filter_passes_the_amplifier_impedance_through():
    omega = np.array([1000.0])
    _, back = thevenin(IdealFilter("Lowpass", order=2, frequency=500.0), omega, 0.5)
    assert back[0] == pytest.approx(0.5)


def test_ideal_filter_amplifier_sees_the_driver_directly():
    omega = np.array([1000.0])
    coil = np.array([32.0 + 5.0j])
    current = np.array([0.5 + 0.1j])
    seen = IdealFilter("Lowpass").amplifier_impedance(coil * current, current, omega)
    assert seen[0] == pytest.approx(coil[0])


def test_empty_passive_ladder_is_a_direct_connection():
    omega = np.array([1000.0, 5000.0])
    gain, back = thevenin(PassiveLadder([]), omega, 1.5)
    assert np.allclose(gain, 1.0)
    assert np.allclose(back, 1.5)


def test_passive_ladder_output_impedance_is_the_shorted_source_impedance():
    """A second-order low-pass looks like its inductor in parallel with its capacitor when
    the amplifier holds the input at zero volts."""
    load, corner = 8.0, 1000.0
    components = synthesise("Butterworth", 2, "Lowpass", corner, load)
    inductance = components[0].value
    capacitance = components[1].value

    omega = np.array([2.0 * math.pi * 300.0])
    _, back = thevenin(PassiveLadder(components), omega)
    z_l = 1j * omega * inductance
    z_c = 1.0 / (1j * omega * capacitance)
    assert back[0] == pytest.approx((z_l * z_c / (z_l + z_c))[0])


def test_passive_ladder_input_impedance_rises_out_of_band():
    """A low-pass fed a high frequency stops passing current, so the amplifier sees a
    light load -- which is how a crossover keeps one driver from wasting the other's
    power."""
    components = synthesise("Butterworth", 2, "Lowpass", 1000.0, 8.0)
    omega = 2.0 * math.pi * np.array([100.0, 20000.0])
    seen = np.abs(loaded_input_impedance(PassiveLadder(components), 8.0, omega))
    assert seen[0] == pytest.approx(8.0, rel=0.05)
    assert seen[1] > 100.0


def test_make_filter_drops_delay_when_passive():
    """No arrangement of inductors and capacitors provides a pure delay."""
    filter_ = make_filter(response="Bypass", passive=True, gain_db=0.0, delay=1e-3)
    assert isinstance(filter_, PassiveLadder)
    assert filter_.components == []


def test_make_filter_turns_gain_into_an_l_pad_when_passive():
    filter_ = make_filter(response="Bypass", passive=True, gain_db=-6.0, impedance=32.0)
    assert [c.kind for c in filter_.components] == ["R", "R"]


def test_describe_reports_component_values():
    text = make_filter(
        response="Lowpass", order=2, frequency=1000.0, passive=True, impedance=8.0
    ).describe()
    assert "series L" in text and "mH" in text
    assert "shunt C" in text and "uF" in text


def test_describe_reports_the_active_shape():
    text = make_filter(response="Highpass", alignment="Bessel", order=3, frequency=2500.0).describe()
    assert "Bessel" in text and "highpass" in text and "2500" in text


def test_summing_response_of_a_complementary_pair_is_flat():
    omega = 2.0 * math.pi * np.logspace(1, 4.3, 200)
    low = make_filter(response="Lowpass", alignment="Linkwitz-Riley", order=4, frequency=2000.0)
    high = make_filter(response="Highpass", alignment="Linkwitz-Riley", order=4, frequency=2000.0)
    assert np.allclose(np.abs(summing_response([low, high], omega)), 1.0, atol=1e-9)


# ---------------------------------------------------------------------------------
# Interaction with a real driver
# ---------------------------------------------------------------------------------


def two_way_driver(name: str, filter_=None, **overrides) -> Driver:
    parameters = DriverParameters.from_thiele_small(
        name=name,
        fs=overrides.get("fs", 60.0),
        Re=32.0,
        Qms=3.0,
        Qes=0.6,
        Sd=20e-4,
        Vas=1.5e-3,
        Le=overrides.get("Le", 0.4e-3),
    )
    return Driver(name, parameters, front_node="Ear", back_node=GROUND,
                  voltage=0.1, filter=filter_)


def solve_with(filter_, frequency=None, **overrides):
    frequency = np.logspace(1, 4.3, 240) if frequency is None else frequency
    network = Network(air.AirProperties.at())
    network.add(two_way_driver("D", filter_, **overrides))
    network.add(Compliance("ear", 100e-6, "Ear"))
    return network.solve(frequency)


def test_a_lowpass_removes_high_frequency_output():
    without = solve_with(None).pressure("Ear")
    with_filter = solve_with(
        make_filter(response="Lowpass", order=2, frequency=500.0)
    ).pressure("Ear")

    low = np.argmin(np.abs(without.frequency - 100.0))
    high = np.argmin(np.abs(without.frequency - 8000.0))
    assert with_filter.spl[low] == pytest.approx(without.spl[low], abs=0.2)
    assert without.spl[high] - with_filter.spl[high] > 40.0


def test_a_passive_filter_damps_the_driver_differently_from_an_ideal_one():
    """The point of modelling a passive crossover at all.

    An ideal filter leaves the driver damped by its own amplifier. A real ladder sits
    between the two, so the amplifier no longer holds the coil at a fixed voltage: the
    electrical damping term weakens, and worse, the filter's own LC resonance is barely
    loaded at all near the driver's impedance peak. The result here is a peak more than
    ten decibels taller than the ideal filter predicts -- which is why real passive
    crossovers need impedance compensation, and why designing one as a bare transfer
    function does not work.
    """
    frequency = np.logspace(1.3, 3, 400)
    corner = 300.0
    active = solve_with(
        make_filter(response="Lowpass", order=2, frequency=corner), frequency
    ).pressure("Ear")
    passive = solve_with(
        make_filter(
            response="Lowpass", order=2, frequency=corner, passive=True, impedance=32.0
        ),
        frequency,
    ).pressure("Ear")

    peak_active = active.spl.max() - np.median(active.spl)
    peak_passive = passive.spl.max() - np.median(passive.spl)
    assert peak_passive > peak_active + 5.0


def test_a_passive_filter_is_not_its_nominal_alignment():
    """A driver is not a resistor, so the loaded response deviates from the target.

    With a voice-coil inductance of 1.5 mH the impedance at 3 kHz is well above the
    nominal 32 ohms the ladder was designed into, so the filter barely attenuates: about
    0.6 dB down where the nominal Butterworth alignment promises 3 dB. Reported rather
    than hidden -- if the two agreed there would be nothing to simulate.
    """
    frequency = np.logspace(2, 4.3, 600)
    corner = 3000.0
    passive = solve_with(
        make_filter(
            response="Lowpass", order=2, frequency=corner, passive=True, impedance=32.0
        ),
        frequency,
        Le=1.5e-3,
    ).pressure("Ear")
    reference = solve_with(None, frequency, Le=1.5e-3).pressure("Ear")

    achieved = passive.spl - reference.spl
    at_corner = achieved[np.argmin(np.abs(frequency - corner))]
    assert at_corner > -3.0103 + 1.0


def test_crossover_does_not_change_a_bypassed_result():
    """A bypass filter must be a true no-op, or every comparison against the un-filtered
    model becomes untrustworthy."""
    frequency = np.logspace(1, 4.3, 100)
    plain = solve_with(None, frequency).pressure("Ear")
    bypassed = solve_with(make_filter(response="Bypass"), frequency).pressure("Ear")
    assert np.allclose(plain.values, bypassed.values)


def test_impedance_is_measured_at_the_filter_input():
    """With a series inductor in front, the terminals present a rising impedance even
    though the voice coil has not changed."""
    frequency = np.array([100.0, 10000.0])
    filter_ = make_filter(
        response="Lowpass", order=1, frequency=1000.0, passive=True, impedance=32.0
    )
    plain = solve_with(None, frequency).input_impedance("D")
    filtered = solve_with(filter_, frequency).input_impedance("D")
    assert np.abs(filtered.values[0]) == pytest.approx(np.abs(plain.values[0]), rel=0.05)
    assert np.abs(filtered.values[1]) > 2.0 * np.abs(plain.values[1])


def test_system_impedance_is_the_drive_voltage_over_the_total_current():
    """What an amplifier sees: one voltage, the sum of the currents it delivers."""
    network = Network(air.AirProperties.at())
    network.add(two_way_driver("Woofer"))
    network.add(two_way_driver("Tweeter"))
    network.add(Compliance("ear", 100e-6, "Ear"))
    solution = network.solve(np.array([1000.0]))

    total = sum(solution.coil_current(name).values for name in ("Woofer", "Tweeter"))
    assert solution.system_impedance().values[0] == pytest.approx(0.1 / total[0], rel=1e-12)


def test_system_impedance_is_not_the_parallel_of_the_branch_impedances():
    """Because the drivers share air, and each changes what the other works against.

    Combining separately-measured branch impedances misses that, for the same reason and
    in the same way that superposing two single-driver models does. The difference is
    small for two identical drivers on a stiff node and it is not zero.
    """
    network = Network(air.AirProperties.at())
    network.add(two_way_driver("Woofer"))
    network.add(two_way_driver("Tweeter"))
    network.add(Compliance("ear", 100e-6, "Ear"))
    solution = network.solve(np.array([80.0]))

    naive = 1.0 / sum(
        1.0 / solution.input_impedance(name).values for name in ("Woofer", "Tweeter")
    )
    assert solution.system_impedance().values[0] != pytest.approx(naive[0], rel=1e-6)


def test_system_impedance_needs_one_amplifier():
    """Drivers on separate amplifiers have no combined impedance, so it is refused."""
    network = Network(air.AirProperties.at())
    network.add(two_way_driver("Woofer"))
    loud = two_way_driver("Tweeter")
    loud.voltage = 0.5
    network.add(loud)
    network.add(Compliance("ear", 100e-6, "Ear"))
    solution = network.solve(np.array([1000.0]))
    with pytest.raises(ValueError, match="not on one"):
        solution.system_impedance()


def test_system_impedance_needs_drivers():
    network = Network(air.AirProperties.at())
    network.add(two_way_driver("D"))
    network.add(Compliance("ear", 100e-6, "Ear"))
    solution = network.solve(np.array([1000.0]))
    with pytest.raises(KeyError):
        solution.system_impedance(["Missing"])


def test_impedance_of_one_branch_ignores_the_other_driver():
    """A silent driver is a load, not a source.

    Reading a branch's impedance from the coupled solve mixes in the neighbour's
    excitation: a tweeter behind a high-pass, shaken by the woofer through the shared
    cavity, came out at 0 ohm before this was separated out.
    """
    frequency = np.array([60.0])
    filtered = make_filter(response="Highpass", alignment="Linkwitz-Riley",
                           order=4, frequency=2000.0)

    network = Network(air.AirProperties.at())
    network.add(two_way_driver("Woofer"))
    network.add(two_way_driver("Tweeter", filtered))
    network.add(Compliance("ear", 100e-6, "Ear"))
    solution = network.solve(frequency)

    impedance = np.abs(solution.input_impedance("Tweeter").values[0])
    assert impedance > 30.0, "a 32 ohm driver cannot present near-zero impedance"


def test_component_describe_covers_every_kind():
    assert "mH" in Component("L", "series", 1e-3).describe()
    assert "uF" in Component("C", "shunt", 1e-6).describe()
    assert "ohm" in Component("R", "series", 4.0).describe()


# ---------------------------------------------------------------------------------
# Terminal quantities
# ---------------------------------------------------------------------------------


def test_terminal_voltage_is_the_drive_voltage_with_no_filter():
    """With nothing between the amplifier and the coil, they are the same thing."""
    frequency = np.logspace(1, 4, 50)
    solution = solve_with(None, frequency)
    assert np.allclose(solution.terminal_voltage("D").values, 0.1)


def test_terminal_voltage_is_neither_the_amplifier_nor_the_nominal_response():
    """A passive filter puts the driver's own impedance into the divider.

    So the voltage at the terminals is not the amplifier's, and it is not the filter's
    nominal transfer function either -- it is what a meter across the driver would read.
    """
    frequency = np.array([2000.0])
    filter_ = make_filter(
        response="Lowpass", order=2, frequency=1000.0, passive=True, impedance=32.0
    )
    solution = solve_with(filter_, frequency)
    measured = abs(solution.terminal_voltage("D").values[0])
    nominal = abs(thevenin(filter_, 2.0 * math.pi * frequency)[0][0]) * 0.1

    assert measured != pytest.approx(0.1)
    assert measured != pytest.approx(nominal, rel=0.01)


def test_ohms_law_holds_at_the_terminals():
    frequency = np.logspace(1, 4, 40)
    filter_ = make_filter(
        response="Lowpass", order=2, frequency=1000.0, passive=True, impedance=32.0
    )
    solution = solve_with(filter_, frequency)
    voltage = solution.terminal_voltage("D").values
    current = solution.coil_current("D").values
    p = two_way_driver("D").parameters
    coil = p.Re + 1j * 2.0 * math.pi * frequency * p.Le
    # V/I at the coil is the blocked impedance plus the motional part, so it is not the
    # blocked impedance alone -- but it must exceed it in magnitude at resonance.
    at_resonance = np.argmin(np.abs(frequency - 60.0))
    assert abs(voltage[at_resonance] / current[at_resonance]) > abs(coil[at_resonance])


def test_a_lossless_ladder_stays_finite_at_its_own_resonance():
    """The reason filters report coefficients rather than a Thevenin equivalent.

    A second-order passive crossover's LC tank resonates at exactly the crossover
    frequency, where the *open-circuit* gain of a lossless ladder is infinite. A user's
    sweep will land on that frequency sooner or later -- it is a round number they typed
    -- and every derived curve would come back NaN from that one point onward.
    """
    corner = 1000.0
    filter_ = make_filter(
        response="Lowpass", order=2, frequency=corner, passive=True, impedance=32.0
    )
    omega = np.array([2.0 * math.pi * corner])

    # The Thevenin form really does diverge here; that is not a bug in the filter.
    gain, alpha, beta = filter_.terminal_coefficients(omega, 0.0)
    assert abs(alpha[0]) < 1e-9

    # Everything the solve actually uses stays finite.
    frequency = np.array([corner * 0.9, corner, corner * 1.1])
    solution = solve_with(filter_, frequency)
    for values in (
        solution.pressure("Ear").values,
        solution.input_impedance("D").values,
        solution.terminal_voltage("D").values,
        solution.coil_current("D").values,
        solution.excursion("D").values,
    ):
        assert np.all(np.isfinite(values))


@pytest.mark.parametrize("order", [2, 4])
def test_no_result_is_nan_anywhere_in_a_dense_sweep(order):
    """A sweep dense enough to land near every ladder resonance."""
    filter_ = make_filter(
        response="Lowpass", order=order, frequency=1000.0, passive=True, impedance=32.0
    )
    frequency = np.concatenate([np.logspace(1, 4.3, 500), [1000.0, 2000.0, 500.0]])
    frequency = np.unique(frequency)
    solution = solve_with(filter_, frequency)
    assert np.all(np.isfinite(solution.pressure("Ear").spl))
    assert np.all(np.isfinite(solution.input_impedance("D").magnitude))


# ---------------------------------------------------------------------------------
# Per-driver contributions
# ---------------------------------------------------------------------------------


def two_way_network(low_order=4, invert=False):
    """Two *identical* drivers split by a complementary pair.

    Identical so the two contributions are level-matched at the crossover frequency,
    which is the only condition under which a polarity error produces a full null rather
    than a partial dip. A real pair is never matched, and the dip is correspondingly
    shallower -- which is exactly why it gets mistaken for a driver problem.
    """
    network = Network(air.AirProperties.at())
    woofer = two_way_driver(
        "Woofer", make_filter(response="Lowpass", alignment="Linkwitz-Riley",
                              order=low_order, frequency=2000.0)
    )
    tweeter = two_way_driver(
        "Tweeter", make_filter(response="Highpass", alignment="Linkwitz-Riley",
                               order=low_order, frequency=2000.0),
    )
    if invert:
        tweeter.polarity = -1
    network.add(woofer)
    network.add(tweeter)
    network.add(Compliance("ear", 100e-6, "Ear"))
    return network


def test_contributions_sum_back_to_the_full_solve():
    """Superposition of *sources* is exact; superposition of models is not.

    Each contribution is taken with the other driver muted but still present, so its
    diaphragm keeps loading the shared cavity. If it were deleted instead, these curves
    would not add up -- which is the whole argument for solving the network at once.
    """
    frequency = np.logspace(1.3, 4.3, 300)
    network = two_way_network()
    parts = network.contributions(frequency, "Ear")
    total = parts["Woofer"].values + parts["Tweeter"].values
    assert np.allclose(total, parts["sum"].values, rtol=1e-9, atol=1e-12)


def test_contributions_leave_the_network_unchanged():
    frequency = np.array([1000.0])
    network = two_way_network()
    before = [d.voltage for d in network.drivers]
    network.contributions(frequency, "Ear")
    assert [d.voltage for d in network.drivers] == before


def test_each_driver_dominates_its_own_band():
    frequency = np.array([100.0, 2000.0, 15000.0])
    parts = two_way_network().contributions(frequency, "Ear")
    woofer, tweeter = parts["Woofer"].spl, parts["Tweeter"].spl
    assert woofer[0] > tweeter[0] + 20.0
    assert tweeter[2] > woofer[2] + 20.0


def test_a_cancelling_pair_sums_below_both_contributors():
    """The signature of a polarity error, and only visible with the parts on one axis."""
    frequency = np.array([2000.0])
    parts = two_way_network(low_order=2).contributions(frequency, "Ear")
    assert parts["sum"].spl[0] < min(parts["Woofer"].spl[0], parts["Tweeter"].spl[0]) - 5.0

    fixed = two_way_network(low_order=2, invert=True).contributions(frequency, "Ear")
    assert fixed["sum"].spl[0] > max(fixed["Woofer"].spl[0], fixed["Tweeter"].spl[0])


def test_contributions_need_a_driver():
    network = Network(air.AirProperties.at())
    network.add(Compliance("a", 1e-3, "X"))
    network.add(Compliance("b", 1e-3, "X", "Y"))
    network.add(Compliance("c", 1e-3, "Y"))
    with pytest.raises(ValueError, match="no drivers"):
        network.contributions(np.array([100.0]), "X")

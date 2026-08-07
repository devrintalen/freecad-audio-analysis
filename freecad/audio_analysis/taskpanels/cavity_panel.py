"""The panel that turns one pick into a cavity.

Modelled on PartDesign's feature panels: the object is created up front, every change
updates it live in the 3D view, and Cancel aborts the transaction so nothing is left
behind. That shape matters more here than it does for a pad. Cavity extraction is a step
where a *plausible wrong answer* is the normal failure -- the wrong region, a cap that
missed, a leak nobody knew about -- and none of those announce themselves in a number.
They are obvious the moment you look at the solid.

So the panel's real output is the picture. The model goes translucent, the cavity is drawn
solid, and the one thing the user is being asked to judge is whether the blue shape is the
volume they meant. A cavity that has quietly swollen to fill the bounding box is
unmistakable that way and invisible in a volume readout, which is why the verdict line says
so in words as well.

**Why the object is created before the panel opens.** The alternative -- previewing with a
temporary shape and only building the real thing on OK -- means maintaining a second code
path that has to stay in step with the first. Creating the real object and aborting the
transaction on Cancel reuses the extraction that recompute already runs, so what you
approve is exactly what you get.

**What is cached, and what is not.** On a twelve-part cup the fuse and cut take about 4 s
and attributing the walls to parts another 4 s. The regions are cached against the inputs
that actually change them -- the parts, the caps, whether hidden bodies count -- so
re-picking a seed, the thing users do repeatedly, skips the boolean and costs only the
attribution. Changing the seed cannot change the regions, only which one is kept.
"""

from __future__ import annotations

from typing import Any, Sequence

import FreeCAD
import FreeCADGui
from PySide import QtCore, QtWidgets

from freecad.audio_analysis import cavity as cavity_lib, leaks, seeding
from freecad.audio_analysis.objects.network_objects import quantity

#: How see-through the surrounding parts go while the panel is open, 0--100.
#: High enough that a cavity buried inside a cup is visible through it, low enough that the
#: parts still read as solid and the user can tell where the cavity sits.
PREVIEW_TRANSPARENCY = 85

#: Transparency of the cavity itself while it is being judged. Lower than the view
#: provider's usual 70: for the duration of the panel the cavity is the subject, not an
#: overlay on the parts.
CAVITY_TRANSPARENCY = 20

#: Edge weight on the previewed cavity. Heavy, because a translucent solid inside a
#: translucent model has no readable silhouette without it, and the edges are seen through
#: the surrounding parts rather than against a clear background.
PREVIEW_LINE_WIDTH = 4.0

#: White, which is the one colour that stays legible here. The edges are drawn over the
#: cavity's own pale blue faces *and* through whatever dimmed parts lie in front of them,
#: so anything tinted competes with one or the other; white separates from both.
PREVIEW_LINE_COLOUR = (1.00, 1.00, 1.00)

#: Below this share of the cavity's wall area, a part is listed but called out as
#: incidental. A screw contributing 0.2% is not something anyone needs to think about.
INCIDENTAL_SHARE = 0.01


def _label(obj: Any) -> str:
    return getattr(obj, "Label", None) or getattr(obj, "Name", "object")


# ---------------------------------------------------------------------------------
# Making the model see-through, and putting it back exactly as it was.
# ---------------------------------------------------------------------------------


class ScenePreview:
    """Turns the surrounding parts translucent, and restores them on close.

    Two mechanisms, because FreeCAD has two. An ordinary shape's view provider carries a
    ``Transparency`` percentage; an ``App::Link`` -- which is what every part of an
    assembly is -- does not, and has to be driven through ``OverrideMaterial`` and
    ``ShapeMaterial`` instead. Assemblies are the case this feature exists for, so getting
    the link path wrong would mean the preview silently does nothing on exactly the models
    that need it.

    Every change is recorded with its previous value and undone on :meth:`restore`.
    Failures are swallowed deliberately: a view property that will not take is a cosmetic
    problem, and it must not stop a cavity being extracted.
    """

    def __init__(self) -> None:
        self._undo: list[tuple[Any, str, Any]] = []

    def _remember(self, vobj: Any, name: str) -> None:
        self._undo.append((vobj, name, getattr(vobj, name)))

    def dim(self, objects: Sequence[Any], transparency: int = PREVIEW_TRANSPARENCY) -> None:
        for obj in objects:
            vobj = getattr(obj, "ViewObject", None)
            if vobj is None:
                continue
            try:
                if hasattr(vobj, "Transparency"):
                    self._remember(vobj, "Transparency")
                    vobj.Transparency = transparency
                elif hasattr(vobj, "ShapeMaterial") and hasattr(vobj, "OverrideMaterial"):
                    self._remember(vobj, "OverrideMaterial")
                    self._remember(vobj, "ShapeMaterial")
                    material = vobj.ShapeMaterial
                    # Material transparency is a 0--1 fraction, not a percentage.
                    material.Transparency = transparency / 100.0
                    vobj.ShapeMaterial = material
                    vobj.OverrideMaterial = True
            except Exception:  # noqa: BLE001 -- view properties vary between builds
                continue

    def highlight(self, obj: Any, transparency: int = CAVITY_TRANSPARENCY) -> None:
        """Bring one object forward: solid, drawn with its edges picked out.

        The heavy outline is what PartDesign's own previews use, and it earns its place
        here for the same reason. A translucent blob inside a translucent model has no
        readable silhouette; the edges are what let you see where the cavity actually
        stops, which is the single judgement this panel is asking for.
        """
        vobj = getattr(obj, "ViewObject", None)
        if vobj is None:
            return
        for name, value in (
            ("Transparency", transparency),
            ("DisplayMode", "Flat Lines"),
            ("LineWidth", PREVIEW_LINE_WIDTH),
            ("LineColor", PREVIEW_LINE_COLOUR),
        ):
            try:
                if hasattr(vobj, name):
                    self._remember(vobj, name)
                    setattr(vobj, name, value)
            except Exception:  # noqa: BLE001 -- cosmetic, and modes vary between builds
                continue

    def restore(self) -> None:
        for vobj, name, value in reversed(self._undo):
            try:
                setattr(vobj, name, value)
            except Exception:  # noqa: BLE001 -- the object may already be gone
                continue
        self._undo.clear()


# ---------------------------------------------------------------------------------
# The panel.
# ---------------------------------------------------------------------------------


class CavityTaskPanel:
    """Seeded cavity extraction, previewed live."""

    def __init__(self, obj: Any, seed: tuple[Any, str] | None = None) -> None:
        self.obj = obj
        self.doc = obj.Document
        self.preview = ScenePreview()
        self._cache_key: tuple | None = None
        self._sources: list[Any] = []
        self._regions: list[Any] = []
        self._hidden: list[str] = []
        # The kept region and the probe that chose it, held so the leak searches can run
        # on demand without repeating the extraction.
        self._region: Any = None
        self._probe: Any = None
        self._verdict_html = "—"

        self.form = self._build()
        self.form.setWindowTitle("Extract cavity")
        # Both searches need solids to work on, and there are none until the first
        # extraction returns. Enabling them before that offers a button that can only fail.
        self._sync_leak_buttons()

        if seed is not None:
            self._set_seed(*seed)

        self._start_preview()
        self._refresh()

    # -- construction ---------------------------------------------------------------

    def _build(self) -> Any:
        form = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(form)

        intro = QtWidgets.QLabel(
            "The connected free space touching your pick, with the parts that bound it "
            "worked out from the result.<br><br>"
            "What gets simulated is the air, not the parts. Check the blue solid is the "
            "volume you meant — if it swells to fill the whole model, a cap is "
            "missing or there is a leak path. To seed from a different face, cancel and "
            "run the command again."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # -- seed. Shown, not editable: re-picking would need a selection observer, and
        # cancelling and running the command again on a different face is both simpler and
        # what PartDesign's own panels do.
        seed_box = QtWidgets.QGroupBox("Seeded from")
        seed_layout = QtWidgets.QVBoxLayout(seed_box)
        self.seed_label = QtWidgets.QLabel("<i>nothing picked</i>")
        self.seed_label.setWordWrap(True)
        self.seed_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        seed_layout.addWidget(self.seed_label)
        layout.addWidget(seed_box)

        # -- parameters
        options = QtWidgets.QGroupBox("Parts")
        grid = QtWidgets.QFormLayout(options)

        self.include_hidden = QtWidgets.QCheckBox("Include hidden bodies")
        self.include_hidden.setChecked(bool(getattr(self.obj, "IncludeHidden", True)))
        self.include_hidden.setToolTip(
            "A part bounds the air whether or not it is shown. Caps are always included."
        )
        self.include_hidden.toggled.connect(lambda _: self._refresh(rebuild=True))
        grid.addRow(self.include_hidden)
        layout.addWidget(options)

        # -- result
        result_box = QtWidgets.QGroupBox("Result")
        result_layout = QtWidgets.QVBoxLayout(result_box)
        self.verdict = QtWidgets.QLabel("—")
        self.verdict.setWordWrap(True)
        result_layout.addWidget(self.verdict)
        self.walls = QtWidgets.QTextEdit()
        self.walls.setReadOnly(True)
        self.walls.setMinimumHeight(120)
        result_layout.addWidget(self.walls)
        result_layout.addWidget(self._leak_tools())
        layout.addWidget(result_box)

        layout.addStretch(1)
        return form

    def _leak_tools(self) -> Any:
        """The two leak searches, offered rather than run.

        Both are far too slow to sit in the refresh path -- roughly half a minute and a
        minute on a full assembly -- and neither is wanted at all unless the cavity failed
        to close. So they are buttons, and the panel says plainly which to reach for
        first, because the cheap one answers the common case and the expensive one does
        not answer it any better.
        """
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)

        blurb = QtWidgets.QLabel(
            "<b>If it leaks, find out where.</b><br>"
            "<b>Scan near misses</b> compares the parts against each other and usually "
            "names the object to fix — typically a cap that stops short of its opening. "
            "It never touches the air, so it is the cheaper search and the one to try "
            "first.<br>"
            "<b>Trace leak path</b> works on the air instead, finding the widest way out "
            "and how narrow that way gets. Several times slower, and it points at a "
            "<i>place</i> rather than a part — worth it when the scan comes up empty, "
            "which is what an opening nobody ever tried to cap looks like."
        )
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        row = QtWidgets.QHBoxLayout()
        self.scan_button = QtWidgets.QPushButton("Scan near misses   (~30 s)")
        self.scan_button.setToolTip(
            "Find every pair of parts that comes within half a millimetre without "
            "meeting, and every cap that overlaps nothing at all. Cost grows with the "
            "number of parts, not with the size of the cavity."
        )
        self.scan_button.clicked.connect(self._scan_near_misses)
        row.addWidget(self.scan_button)

        self.trace_button = QtWidgets.QPushButton("Trace leak path   (~1 min)")
        self.trace_button.setToolTip(
            f"Voxelise the cavity at {leaks.DEFAULT_RESOLUTION_MM} mm and find the route "
            f"to the outside whose narrowest point is widest. Cannot see a gap finer than "
            f"the voxel size, so 'no route' is not proof of a seal."
        )
        self.trace_button.clicked.connect(self._trace_leak)
        row.addWidget(self.trace_button)
        layout.addLayout(row)

        self.findings = QtWidgets.QTextEdit()
        self.findings.setReadOnly(True)
        self.findings.setMinimumHeight(160)
        self.findings.setVisible(False)
        layout.addWidget(self.findings)
        return widget

    # -- preview -------------------------------------------------------------------

    def _scene_objects(self) -> list[Any]:
        """Everything in the document worth dimming: solids other than this cavity."""
        found = []
        for obj in self.doc.Objects:
            if obj is self.obj or seeding.is_air_object(obj):
                continue
            shape = getattr(obj, "Shape", None)
            if shape is None or not hasattr(shape, "isNull") or shape.isNull():
                continue
            if not shape.Solids:
                continue
            found.append(obj)
        return found

    def _start_preview(self) -> None:
        self.preview.dim(self._scene_objects())
        self.preview.highlight(self.obj)

    # -- the seed ------------------------------------------------------------------

    def _set_seed(self, source: Any, subname: str) -> bool:
        """Record the pick. Returns whether it was accepted, so a refusal is not acted on."""
        try:
            self.obj.Seed = (source, [subname])
        except Exception as exc:  # noqa: BLE001 -- link property may refuse the object
            FreeCAD.Console.PrintError(
                f"Audio Analysis: could not use that pick as a seed: {exc}\n"
            )
            return False
        from freecad.audio_analysis.capping import reference_label

        try:
            shown = reference_label(source, subname)
        except Exception:  # noqa: BLE001 -- naming is cosmetic
            shown = f"{_label(source)}.{subname}"
        self.seed_label.setText(f"<b>{shown}</b>")
        return True

    # -- extraction ----------------------------------------------------------------

    def _boundary_objects(self) -> tuple[list[Any], list[Any]]:
        """The parts and the caps this cavity should be built from.

        Scope follows the pick: a face picked inside an assembly means that assembly is
        the model. Caps come from the whole document regardless, because they belong to
        the analysis rather than to the CAD.
        """
        seed = getattr(self.obj, "Seed", None)
        seed_obj = seed[0] if seed else None
        caps = [o for o in self.doc.Objects if seeding.is_cap_object(o)]

        if seed_obj is not None and seed_obj not in caps:
            scope = [seed_obj]
        else:
            scope = [
                o
                for o in self.doc.RootObjects
                if o is not self.obj
                and not seeding.is_air_object(o)
                and not seeding.is_cap_object(o)
            ]
        return scope, caps

    def _status(self, message: str) -> None:
        """Say what is happening, and let Qt actually paint it.

        The extraction blocks the event loop for several seconds, so without the explicit
        repaint the message never appears and the panel simply freezes. Deliberately does
        *not* record the text as the verdict: a progress line is not a result, and
        :meth:`_restore_verdict` has to be able to put the real one back.
        """
        self.verdict.setText(f"<i>{message}</i>")
        QtWidgets.QApplication.processEvents()

    def _set_verdict(self, html: str) -> None:
        self._verdict_html = html
        self.verdict.setText(html)

    def _restore_verdict(self) -> None:
        self.verdict.setText(self._verdict_html)

    def _refresh(self, rebuild: bool = False) -> None:
        """Re-extract if the inputs changed, then re-match the seed and redraw."""
        seed = getattr(self.obj, "Seed", None)
        if not seed or not seed[1]:
            # Only reachable by editing a cavity made before seeding existed, which still
            # selects its region by RegionIndex. There is nothing to pick with here, so
            # say what to do rather than offering a control that is not present.
            self._set_verdict(
                "This cavity has no seed — it keeps region "
                f"{getattr(self.obj, 'RegionIndex', 0)} by number. Cancel and run "
                "<b>Extract cavity</b> with a face selected to seed it by position, "
                "which survives a rebuild."
            )
            self.walls.setPlainText("")
            return

        boundary, caps = self._boundary_objects()
        include_hidden = self.include_hidden.isChecked()
        key = (
            tuple(o.Name for o in boundary),
            tuple(o.Name for o in caps),
            include_hidden,
        )

        # One override cursor for the whole operation. Setting it per stage would stack --
        # Qt keeps a stack of override cursors and only pops one per restore, so a wait
        # cursor left on the stack outlives the panel and follows the user around the
        # application.
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            if rebuild or key != self._cache_key:
                self._extract(boundary, caps, include_hidden)
                self._cache_key = key
            self._apply_seed()
        except cavity_lib.BooleanFailure as exc:
            self._region = self._probe = None
            self._set_verdict(
                "<b style='color:#b00'>Extraction failed.</b> The boundary parts could "
                "not be combined, so nothing can be concluded about whether the cavity "
                "is closed."
            )
            self.walls.setPlainText(cavity_lib.format_diagnostics(exc.diagnostics))
        except cavity_lib.CavityError as exc:
            self._region = self._probe = None
            self._set_verdict(f"<b style='color:#b00'>{exc}</b>")
            self.walls.setPlainText("")
        except Exception as exc:  # noqa: BLE001 -- boundary with Qt's event loop
            self._region = self._probe = None
            self._set_verdict(f"<b style='color:#b00'>Unexpected failure: {exc}</b>")
            self.walls.setPlainText("")
            FreeCAD.Console.PrintError(f"Audio Analysis: cavity preview failed: {exc}\n")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._sync_leak_buttons()

    def _extract(self, boundary: list[Any], caps: list[Any], include_hidden: bool) -> None:
        self._status("Collecting parts…")
        self._sources, self._hidden = seeding.solids_for(
            list(boundary) + list(caps), include_hidden=include_hidden
        )
        self._status(f"Subtracting {len(self._sources)} solids… this takes a few seconds.")
        self._regions = cavity_lib.extract_regions_from_solids(
            self._sources,
            padding=self.obj.Padding.getValueAs("mm").Value,
            minimum_volume=self.obj.MinimumVolume.getValueAs("mm^3").Value,
        )

    def _apply_seed(self) -> None:
        source, subnames = self.obj.Seed
        probe = seeding.probe_from_reference(source, subnames[0])
        region = seeding.region_for_probe(self._regions, probe)
        self._region, self._probe = region, probe

        if region is None:
            self.obj.Shape = self._empty_shape()
            self._set_verdict(
                "<b style='color:#b00'>That face touches no air.</b> It is probably "
                "buried against another part — pick a face on the side the cavity "
                "is on."
            )
            self.walls.setPlainText("")
            return

        self.obj.Shape = region.shape
        self.obj.Volume = quantity(region.volume_mm3, "mm^3")

        self._status("Working out which parts bound it…")
        parts, unattributed = seeding.wetted_parts(region, self._sources)

        self._show_verdict(region, parts)
        self.walls.setPlainText(self._wall_text(parts, unattributed))

    @staticmethod
    def _empty_shape() -> Any:
        import Part

        return Part.Shape()

    def _show_verdict(self, region: Any, parts: Sequence[Any]) -> None:
        if region.is_exterior:
            self._set_verdict(
                f"<b style='color:#b00'>Leaks to the outside — "
                f"{region.volume_cm3:.3f} cm³.</b><br>"
                f"This air reaches the edge of the model, so it is continuous with the "
                f"outside and is not a cavity. A cap is missing, or there is a leak path. "
                f"The parts below are everything it touches — the two searches at the "
                f"bottom will find <i>where</i>."
            )
            return

        box = region.shape.BoundBox
        self._set_verdict(
            f"<b style='color:#060'>Enclosed — {region.volume_cm3:.3f} cm³.</b>"
            f"<br>{box.XLength:.1f} × {box.YLength:.1f} × {box.ZLength:.1f} mm, "
            f"bounded by {len(parts)} part(s)."
        )

    def _wall_text(self, parts: Sequence[Any], unattributed: float) -> str:
        lines = []
        if self._hidden:
            lines.append(
                "Skipped as hidden: " + ", ".join(sorted(set(self._hidden)))
                + "\n(these bound the air too — tick 'Include hidden bodies')\n"
            )
        if not parts:
            lines.append("No bounding parts identified.")
            return "\n".join(lines)

        total = sum(p.area_mm2 for p in parts) + unattributed
        lines.append("Bounded by, as a share of the cavity's wall:")
        for part in parts:
            share = part.area_mm2 / total if total else 0.0
            note = "   (incidental)" if share < INCIDENTAL_SHARE else ""
            lines.append(f"  {part.label}: {100.0 * share:.1f}%{note}")
        if unattributed > 0.0:
            lines.append(
                f"  unattributed: {100.0 * unattributed / total:.1f}% "
                f"— wall belonging to no part"
            )
        return "\n".join(lines)

    # -- the leak searches -------------------------------------------------------------

    def _sync_leak_buttons(self) -> None:
        """Neither search means anything before an extraction has produced solids."""
        ready = bool(self._sources)
        for button in (self.scan_button, self.trace_button):
            button.setEnabled(ready)

    def _show_findings(self, text: str) -> None:
        self.findings.setPlainText(text)
        self.findings.setVisible(True)

    def _run(self, message: str, work: Any) -> Any:
        """Run a slow search with the cursor and the status line saying so.

        One override cursor, popped in ``finally``: Qt stacks them and only pops one per
        restore, so a leaked wait cursor outlives the panel and follows the user around
        the whole application.
        """
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self._status(message)
            return work()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._restore_verdict()

    def _scan_near_misses(self) -> None:
        if not self._sources:
            self._show_findings("Nothing to scan yet — the extraction has not run.")
            return

        caps = [_label(o) for o in self.doc.Objects if seeding.is_cap_object(o)]
        try:
            found = self._run(
                f"Comparing {len(self._sources)} solids against each other…",
                lambda: leaks.near_miss_diagnostics(self._sources, caps=caps),
            )
        except Exception as exc:  # noqa: BLE001 -- boundary with Qt's event loop
            self._show_findings(f"The scan failed: {exc}")
            FreeCAD.Console.PrintError(f"Audio Analysis: near-miss scan failed: {exc}\n")
            return

        text = leaks.describe_near_misses(found)
        if found:
            text += "\n\n" + cavity_lib.format_diagnostics(found)
        self._show_findings(text)

    def _trace_leak(self) -> None:
        if self._region is None or self._probe is None:
            self._show_findings("Nothing to trace yet — the extraction has not run.")
            return
        if not self._region.is_exterior:
            # Tracing an enclosed cavity can only report "no way out", which the verdict
            # already says. Spending a minute to repeat it would be a poor trade.
            self._show_findings(
                "This cavity is already enclosed, so there is no leak to trace.\n\n"
                "The trace looks for a route from the cavity to the edge of the model, "
                "and there is none — which is what the verdict above says. Run it on a "
                "cavity that reports leaking."
            )
            return

        try:
            envelope = cavity_lib.make_envelope(
                [s.solid for s in self._sources],
                self.obj.Padding.getValueAs("mm").Value,
            )
            found = self._run(
                f"Voxelising {self._region.volume_cm3:.0f} cm³ at "
                f"{leaks.DEFAULT_RESOLUTION_MM} mm… this takes about a minute.",
                lambda: leaks.find_escape_path(
                    self._region.shape,
                    self._probe.point,
                    envelope_box=envelope.BoundBox,
                ),
            )
        except leaks.LeakSearchError as exc:
            self._show_findings(f"The trace could not run: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 -- boundary with Qt's event loop
            self._show_findings(f"The trace failed: {exc}")
            FreeCAD.Console.PrintError(f"Audio Analysis: leak trace failed: {exc}\n")
            return

        self._show_findings(
            leaks.describe_escape_path(found, leaks.DEFAULT_RESOLUTION_MM)
        )

    # -- FreeCAD task-dialog protocol ------------------------------------------------

    def getStandardButtons(self) -> int:
        return int(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )

    def _stop(self) -> None:
        """Undim the model. Always before touching the document.

        Order matters on Cancel: the restore refers to the cavity's own view provider, and
        aborting the transaction deletes the cavity.
        """
        self.preview.restore()

    @staticmethod
    def _close_dialog() -> None:
        try:
            FreeCADGui.Control.closeDialog()
        except Exception:  # noqa: BLE001 -- already closing
            pass

    def accept(self) -> bool:
        """Commit: write the settings that were previewed, and keep the object."""
        self._stop()

        self.obj.IncludeHidden = self.include_hidden.isChecked()
        boundary, caps = self._boundary_objects()
        self.obj.Boundary = boundary
        self.obj.Caps = caps

        # Re-extract rather than keeping the preview's shape. This is the code path the
        # object takes on every later rebuild, so if it disagrees with what was previewed
        # that is worth finding out now rather than at some later recompute.
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.obj.Proxy.extract(self.obj)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.doc.commitTransaction()
        # The extraction above is the recompute. Without purging the touch it left behind,
        # the document recompute below runs the whole ten-second boolean a second time for
        # an answer it already has.
        self.obj.purgeTouched()
        self.doc.recompute()
        self._close_dialog()

        FreeCAD.Console.PrintMessage(
            f"Audio Analysis: {self.obj.Label} = "
            f"{self.obj.Volume.getValueAs('cm^3').Value:.3f} cm3\n"
            f"{self.obj.BoundedBy}\n"
        )
        return True

    def reject(self) -> bool:
        """Cancel: abort the transaction, which removes the object entirely."""
        self._stop()
        self.doc.abortTransaction()
        self.doc.recompute()
        self._close_dialog()
        return True

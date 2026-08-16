"""Can a phosphoserine reach Boltz through Proto? Today: no, silently.

This exists because of a specific unresolved question. The ELK1-MED23
calibration control failed (single-sample ipTM 0.31 against a 0.60 threshold),
and the published interaction is phosphorylation-dependent: Ser383 of ELK1 is
phosphorylated in the deposited complex, and the consensus module recovers that
residue from the experimental coordinates. So the failure has two candidate
explanations that a single number cannot separate — Boltz cannot model this
interface, or Boltz was never told about the phosphate.

That run has since been withdrawn for an unrelated reason: the partner
accession was O75448, which is MED24 rather than MED23, so it modelled the
wrong protein and scored the result against MED23 numbering. The finding below
survives that withdrawal and applies to any corrected re-run, because it is
about what the input format can express rather than what was put in it.

The answer turns out to be that the phosphate cannot be supplied at all, and
the way it cannot is the dangerous kind:

  Boltz-2 supports modified residues in its input YAML.
  proto_tools models them — `Chain.modifications`, validated against the actual
  residue at that position, so `SEP` on a leucine is rejected.
  `complex_to_yaml` then never serialises them.

`chain.has_modifications()` returns True and the YAML says nothing. No warning,
no error. A run set up with pSer383 is indistinguishable from one without, and
would have been reported as a phosphorylated control.

The YAML is built inside the Modal container from the deserialised input, so
this cannot be patched from here — it is upstream. Until it is fixed, the ELK1
control is necessarily unphosphorylated and its failure is confounded: it is
not evidence about Boltz's capability on this interface, and must not be cited
as such.

These tests assert the CURRENT broken behaviour on purpose. When proto_tools
starts emitting modifications, `test_modifications_are_dropped` fails — and
that failure is the signal to re-run the control properly and re-open the
question.
"""
from __future__ import annotations

import unittest

try:
    from proto_tools.entities.complex import Chain
    from proto_tools.tools.structure_prediction.boltz2.helpers import complex_to_yaml
    HAVE_PROTO = True
except Exception:
    HAVE_PROTO = False

# ELK1 374-384 in the deposited complex. Ser383 carries the phosphate that makes
# the MED23 interaction phosphorylation-dependent.
MOTIF = "PSIHFWSTLSP"
PSER_OFFSET = 10   # 1-indexed position of the phosphorylated serine within MOTIF


@unittest.skipUnless(HAVE_PROTO, "proto_tools not installed")
class ModificationSupportTests(unittest.TestCase):
    def test_a_modification_can_be_attached_and_is_validated(self):
        """Proto does model this properly, which is why the drop is so easy to
        miss: everything up to serialisation behaves correctly."""
        c = Chain(id="B", sequence=MOTIF, entity_type="protein")
        c.add_modification(position=PSER_OFFSET, modification_code="SEP")
        self.assertTrue(c.has_modifications())
        self.assertEqual(c.modifications[0].modification_code, "SEP")

    def test_a_modification_on_the_wrong_residue_is_rejected(self):
        """Position 1 of the motif is a proline, not a serine."""
        c = Chain(id="B", sequence=MOTIF, entity_type="protein")
        with self.assertRaises(ValueError):
            c.add_modification(position=1, modification_code="SEP")

    def test_modifications_are_dropped(self):
        """The bug. Failing here is GOOD NEWS — see the module docstring.

        If this starts failing, proto_tools has begun emitting modifications:
        re-run the ELK1 control with pSer383 and the confound is resolved.
        """
        c = Chain(id="B", sequence=MOTIF, entity_type="protein")
        c.add_modification(position=PSER_OFFSET, modification_code="SEP")
        yaml_sent = complex_to_yaml([c])

        self.assertTrue(c.has_modifications(),
                        "the chain still carries the modification")
        self.assertNotIn("SEP", yaml_sent,
                         "proto_tools now emits modifications — re-run the ELK1 "
                         "control with pSer383 and update the calibration record")
        self.assertNotIn("modification", yaml_sent.lower())

    def test_the_sequence_itself_still_survives(self):
        """So the drop is specific to modifications, not a broken serialiser."""
        c = Chain(id="B", sequence=MOTIF, entity_type="protein")
        c.add_modification(position=PSER_OFFSET, modification_code="SEP")
        self.assertIn(MOTIF, complex_to_yaml([c]))


if __name__ == "__main__":
    unittest.main()

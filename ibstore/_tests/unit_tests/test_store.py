import json
import random
import tempfile

import numpy
import pytest
from openff.qcsubmit.results import OptimizationResultCollection
from openff.toolkit import Molecule
from openff.utilities import get_data_file_path, temporary_cd

from ibstore._store import MoleculeStore
from ibstore.exceptions import DatabaseExistsError
from ibstore.models import MMConformerRecord, QMConformerRecord


def test_from_qcsubmit(small_collection):
    db = "foo.sqlite"
    with temporary_cd():
        store = MoleculeStore.from_qcsubmit_collection(small_collection, db)

        # Sanity check molecule deduplication
        assert len(store.get_smiles()) == len({*store.get_smiles()})

        # Ensure a new object can be created from the same database
        assert len(MoleculeStore(db)) == len(store)


def test_do_not_overwrite(small_collection):
    with tempfile.NamedTemporaryFile(suffix=".sqlite") as file:
        with pytest.raises(DatabaseExistsError, match="already exists."):
            MoleculeStore.from_qcsubmit_collection(
                small_collection,
                file.name,
            )


def test_load_existing_database(small_store):
    assert len(small_store) == 40


def test_get_molecule_ids(small_store):
    molecule_ids = small_store.get_molecule_ids()

    assert len(molecule_ids) == len({*molecule_ids}) == 40

    assert min(molecule_ids) == 1
    assert max(molecule_ids) == 40


def test_get_molecule_id_by_qcarchive_id(small_store):
    molecule_id = 40
    qcarchive_id = small_store.get_qcarchive_ids_by_molecule_id(molecule_id)[-1]

    assert small_store.get_molecule_id_by_qcarchive_id(qcarchive_id) == molecule_id


def test_molecules_sorted_by_qcarchive_id():
    raw_ch = json.load(
        open(get_data_file_path("_tests/data/01-processed-qm-ch.json", "ibstore")),
    )

    random.shuffle(raw_ch["entries"]["https://api.qcarchive.molssi.org:443/"])

    with tempfile.NamedTemporaryFile(mode="w+") as file:
        json.dump(raw_ch, file)
        file.flush()

        store = MoleculeStore.from_qcsubmit_collection(
            OptimizationResultCollection.parse_file(file.name),
            database_name=tempfile.NamedTemporaryFile(suffix=".sqlite").name,
        )

        qcarchive_ids = store.get_qcarchive_ids_by_molecule_id(40)

    for index, id in enumerate(qcarchive_ids[:-1]):
        assert id < qcarchive_ids[index + 1]


def test_get_conformers(small_store):
    force_field = "openff-2.0.0"
    molecule_id = 40
    qcarchive_id = small_store.get_qcarchive_ids_by_molecule_id(molecule_id)[-1]

    numpy.testing.assert_allclose(
        small_store.get_qm_conformer_by_qcarchive_id(
            qcarchive_id,
        ),
        small_store.get_qm_conformers_by_molecule_id(molecule_id)[-1],
    )

    numpy.testing.assert_allclose(
        small_store.get_mm_conformer_by_qcarchive_id(
            qcarchive_id,
            force_field=force_field,
        ),
        small_store.get_mm_conformers_by_molecule_id(
            molecule_id,
            force_field=force_field,
        )[-1],
    )


def test_get_force_fields(small_store):
    force_fields = small_store.get_force_fields()

    assert len(force_fields) == 9

    assert "openff-2.1.0" in force_fields
    assert "gaff-2.11" in force_fields
    assert "openff-3.0.0" not in force_fields


def test_get_mm_conformer_records_by_molecule_id(small_store, diphenylvinylbenzene):
    records = small_store.get_mm_conformer_records_by_molecule_id(
        1,
        force_field="openff-2.1.0",
    )

    for record in records:
        assert isinstance(record, MMConformerRecord)
        assert record.molecule_id == 1
        assert record.force_field == "openff-2.1.0"
        assert record.coordinates.shape == (36, 3)
        assert record.energy is not None

        assert Molecule.from_mapped_smiles(record.mapped_smiles).is_isomorphic_with(
            diphenylvinylbenzene,
        )


def test_get_qm_conformer_records_by_molecule_id(small_store, diphenylvinylbenzene):
    records = small_store.get_qm_conformer_records_by_molecule_id(1)

    for record in records:
        assert isinstance(record, QMConformerRecord)
        assert record.molecule_id == 1
        assert record.coordinates.shape == (36, 3)
        assert record.energy is not None

        assert Molecule.from_mapped_smiles(record.mapped_smiles).is_isomorphic_with(
            diphenylvinylbenzene,
        )


@pytest.mark.parametrize(("molecule_id", "expected_len"), [(28, 1), (40, 9)])
def test_get_mm_energies_by_molecule_id(
    small_store,
    molecule_id,
    expected_len,
):
    """Trigger issue #16."""
    energies = small_store.get_mm_energies_by_molecule_id(
        molecule_id,
        force_field="openff-2.0.0",
    )

    for energy in energies:
        assert isinstance(energy, float)

    assert len(energies) == expected_len


@pytest.mark.parametrize(("molecule_id", "expected_len"), [(28, 1), (40, 9)])
def test_get_qm_energies_by_molecule_id(
    small_store,
    molecule_id,
    expected_len,
):
    energies = small_store.get_qm_energies_by_molecule_id(molecule_id)

    for energy in energies:
        assert isinstance(energy, float)

    assert len(energies) == expected_len

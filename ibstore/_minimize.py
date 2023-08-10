from collections import defaultdict
from multiprocessing import Pool
from typing import Union

import numpy
import openmm
import openmm.app
import openmm.unit
from openff.toolkit import ForceField, Molecule
from openff.toolkit.utils import OpenEyeToolkitWrapper
from openff.toolkit.utils.toolkit_registry import _toolkit_registry_manager
from pydantic import Field
from tqdm import tqdm

from ibstore._base.array import Array
from ibstore._base.base import ImmutableModel

N_PROCESSES = 10

FORCE_FIELDS: dict[str, ForceField] = {
    "openff-1.0.0": ForceField("openff_unconstrained-1.0.0.offxml"),
    "openff-1.3.0": ForceField("openff_unconstrained-1.3.0.offxml"),
    "openff-2.0.0": ForceField("openff_unconstrained-2.0.0.offxml"),
    "openff-2.1.0": ForceField("openff_unconstrained-2.1.0.offxml"),
}


def _minimize_blob(
    input: dict[str, dict[str, Union[str, numpy.ndarray]]],
    force_field: str,
) -> dict[str, list["MinimizationResult"]]:
    returned = defaultdict(list)
    inputs = list()

    with _toolkit_registry_manager(OpenEyeToolkitWrapper()):
        for inchi_key in input:
            for row in input[inchi_key]:
                inputs.append(
                    MinimizationInput(
                        inchi_key=inchi_key,
                        qcarchive_id=row["qcarchive_id"],
                        force_field=force_field,
                        mapped_smiles=row["mapped_smiles"],
                        coordinates=row["coordinates"],
                    )
                )

        with Pool(processes=N_PROCESSES) as pool:
            for result in tqdm(
                pool.imap(
                    _run_openmm,
                    inputs,
                ),
                desc="Building and minimizing systems",
                total=len(inputs),
            ):
                returned[result.inchi_key].append(result)

    return returned


class MinimizationInput(ImmutableModel):
    inchi_key: str = Field(..., description="The InChI key of the molecule")
    qcarchive_id: str = Field(
        ...,
        description="The ID of the molecule in the QCArchive",
    )
    force_field: str = Field(
        ...,
        description="And identifier of the force field to use for the minimization",
    )
    mapped_smiles: str = Field(
        ...,
        description="The mapped SMILES string for the molecule, stored to track atom maps",
    )
    coordinates: Array = Field(
        ...,
        description="The coordinates [Angstrom] of this conformer with shape=(n_atoms, 3).",
    )


class MinimizationResult(ImmutableModel):
    inchi_key: str = Field(..., description="The InChI key of the molecule")
    qcarchive_id: str
    force_field: str
    mapped_smiles: str
    coordinates: Array
    energy: float = Field(..., description="Minimized energy in kcal/mol")


def _run_openmm(
    input: MinimizationInput,
) -> MinimizationResult:
    inchi_key: str = input.inchi_key
    qcarchive_id: str = input.qcarchive_id
    positions: numpy.ndarray = input.coordinates

    molecule = Molecule.from_inchi(inchi_key)

    molecule_from_smiles = Molecule.from_mapped_smiles(input.mapped_smiles)

    are_isomorphic, atom_map = Molecule.are_isomorphic(
        molecule,
        molecule_from_smiles,
        return_atom_map=True,
    )

    assert are_isomorphic, (
        "Molecules from InChi and mapped SMILES are not isomorphic:\n"
        f"\tinchi_key={inchi_key}\n"
        f"\tmapped_smiles={input.mapped_smiles}"
    )

    molecule.remap(mapping_dict=atom_map)

    system = FORCE_FIELDS[input.force_field].create_openmm_system(
        molecule.to_topology()
    )

    context = openmm.Context(
        system,
        openmm.VerletIntegrator(0.1 * openmm.unit.femtoseconds),
        openmm.Platform.getPlatformByName("Reference"),
    )

    context.setPositions(
        (positions * openmm.unit.angstrom).in_units_of(openmm.unit.nanometer)
    )
    openmm.LocalEnergyMinimizer.minimize(context, 5.0e-9, 1500)

    return MinimizationResult(
        inchi_key=inchi_key,
        qcarchive_id=qcarchive_id,
        force_field=input.force_field,
        mapped_smiles=input.mapped_smiles,
        coordinates=context.getState(getPositions=True)
        .getPositions()
        .value_in_unit(openmm.unit.angstrom),
        energy=context.getState(
            getEnergy=True,
        )
        .getPotentialEnergy()
        .value_in_unit(
            openmm.unit.kilocalorie_per_mole,
        ),
    )
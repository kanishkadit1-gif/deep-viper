# Assets

This directory holds the Franka Panda model files used by `generate_scene.py`.

## Option A — Official Franka URDF meshes (recommended)

1. Clone the official Franka description package:
   ```
   git clone https://github.com/frankarobotics/franka_description.git
   ```

2. Copy the visual DAE mesh files into `panda_meshes/`:
   ```
   mkdir panda_meshes
   cp franka_description/meshes/visual/link*.dae panda_meshes/
   ```
   You need: `link0.dae` through `link8.dae` + `hand.dae`

3. The generator will auto-detect and import them.
   If the folder is missing, it falls back to the procedural arm.

Also copy the URDF for Phase 3 IK solver:
   ```
   cp franka_description/robots/panda.urdf panda.urdf
   ```

## Option B — Procedural arm (no download needed)

If `panda_meshes/` does not exist, `generate_scene.py` automatically
builds a procedural Franka-like arm from cylinders and spheres
using correct Panda proportions. Good enough for data generation.

## Directory layout after setup

```
assets/
├── README.md
├── panda.urdf              <- for Phase 3 IK solver (ikpy)
└── panda_meshes/
    ├── link0.dae
    ├── link1.dae
    ├── ...
    ├── link7.dae
    └── hand.dae
```

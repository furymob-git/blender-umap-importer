# Blender UMAP Importer

A generic, high-performance Blender extension that rebuilds Unreal Engine (UE4 / UE5) levels from FModel `.umap` JSON exports. Compatible with Blender **3.x**, **4.x**, and **5.x**.

---

<p align="center">
  <img src="https://img.shields.io/badge/Blender-3.x | 4.x | 5.x-orange?logo=blender&style=for-the-badge" alt="Blender Version">
  <img src="https://img.shields.io/badge/Unreal Engine-4.x | 5.x-blue?logo=unrealengine&style=for-the-badge" alt="Unreal Engine">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="MIT License">
</p>

---

## Key Features

*   **Actor Hierarchy & Transforms**: Automatically recreates Unreal's Level Actor-Component parent trees and converts coordinates from Left-Handed (cm) to Blender's Right-Handed (meters) system.
*   **HISM / ISM Mesh Instancing**: Installs base meshes once and creates lightweight duplicates for repeat geometries (e.g., foliage, repeating architecture props), keeping viewports fast and file sizes low.
*   **Smart PBR Materials**: Auto-wires Principled BSDF nodes for BaseColor, Alpha, Roughness, and Metallic (ORM/MRA masks), with automatic Normal map Green-channel inversion.
*   **Vertex Color Blending**: Supports complex blended materials (mix shaders powered by mesh vertex color attributes).
*   **Decals & Collisions**: Automates decal projection planes with a 0.5cm offset to eliminate Z-fighting, and automatically hides physics collision meshes (`UCX_`, `UBX_`, etc.).
*   **Live Asset Analysis UI**: Dynamic panel showing found vs missing assets directly in Blender. Includes a **Folder Depth** slider to group paths for easy bulk folder extraction in FModel.

---

## Installation

1. Download [blender_umap_importer.py](blender_umap_importer.py).
2. Open Blender, go to **Edit > Preferences > Add-ons** (or **Get Extensions > Install from Disk** in Blender 4.2+).
3. Select the Python file and enable **Object: UMAP Importer**.
4. Press `N` in the 3D Viewport to open the sidebar, and select the **UMAP Importer** tab.

---

## Quick Start

1. **Unreal level JSON**: Export your level JSON from FModel.
2. **Assets Folder**: Export your 3D models (`.glb`/`.gltf`/`.fbx`) and textures.
3. **Analyze Assets**: 
   * Select your level JSON and asset directory.
   * Expand the **Asset Analysis / Missing Folders** panel.
   * Adjust **Folder Depth** (e.g., to `2` or `1`) to group subdirectories.
   * Export the detailed report if you want a text list of what is missing.
4. **Import**: Click **Import Level**.

---

> [!IMPORTANT]
> ### Disclaimer & Legal Info
> 
> *   **Purpose**: This addon is developed solely for educational, personal research, rendering fan art, and level design/modding purposes.
> *   **No Assets Distributed**: This repository does not contain, host, or distribute any copyrighted game files, models, textures, or JSON levels.
> *   **User Responsibility**: The user is solely responsible for compliance with the terms of service, End User License Agreements (EULA), and copyright laws of the respective games they choose to import assets from. 
> *   **Affiliation**: This tool is an independent open-source project and is not affiliated, associated, or endorsed by Epic Games or any other game publishers.

---

## License

This project is open-source and licensed under the [MIT License](LICENSE).

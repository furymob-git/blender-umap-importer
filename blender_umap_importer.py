bl_info = {
    "name": "UMAP Importer",
    "author": "FURYMOB & Gemini",
    "version": (1, 3, 0),
    "blender": (4, 0, 0),
    "category": "Object",
    "description": "Addon that imports Unreal Engine .umap files from exported JSON data into Blender with hierarchy, instances, decals, and vertex-blended materials",
}

import bpy
import json
import os
import math
import mathutils
import time

def update_analysis(self, context):
    try:
        run_asset_analysis(context.scene.my_tool)
    except Exception as e:
        print(f"Error in update_analysis: {e}")


class LIS_ReferencedFolderItem(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty()
    missing: bpy.props.IntProperty()
    missing_meshes: bpy.props.IntProperty()
    missing_materials: bpy.props.IntProperty()
    missing_levels: bpy.props.IntProperty()
    missing_mesh_names: bpy.props.StringProperty()      # newline-separated, max 15
    missing_material_names: bpy.props.StringProperty()  # newline-separated, max 15
    missing_level_names: bpy.props.StringProperty()     # newline-separated, max 15
    total: bpy.props.IntProperty()

class LIS_SubLevelItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    package_path: bpy.props.StringProperty()
    enabled: bpy.props.BoolProperty(default=True, update=update_analysis)
    found: bpy.props.BoolProperty(default=True)

class LIS_UL_sublevels(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "enabled", text="")
            if item.found:
                row.label(text=item.name, icon="SCENE_DATA")
            else:
                row.label(text=f"{item.name} (Missing)", icon="ERROR")

class LIS_UL_referenced_folders(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            if item.missing > 0:
                split = row.split(factor=0.75, align=True)
                split.label(text=item.path, icon="FOLDER_REDIRECT")
                badge_row = split.row(align=True)
                badge_row.alignment = 'RIGHT'
                if item.missing_levels > 0:
                    op = badge_row.operator("lis.show_missing_info", text=str(item.missing_levels), icon="SCENE_DATA", emboss=False)
                    op.names = item.missing_level_names
                    op.label = f"{item.missing_levels} missing sublevel(s)"
                if item.missing_meshes > 0:
                    op = badge_row.operator("lis.show_missing_info", text=str(item.missing_meshes), icon="MESH_DATA", emboss=False)
                    op.names = item.missing_mesh_names
                    op.label = f"{item.missing_meshes} missing mesh(es)"
                if item.missing_materials > 0:
                    op = badge_row.operator("lis.show_missing_info", text=str(item.missing_materials), icon="MATERIAL", emboss=False)
                    op.names = item.missing_material_names
                    op.label = f"{item.missing_materials} missing material(s)"
            else:
                row.label(text=f"{item.path} ({item.total} assets)", icon="FILE_FOLDER")
        elif self.layout_type == 'GRID':
            pass


class ShowMissingInfoOperator(bpy.types.Operator):
    bl_idname = "lis.show_missing_info"
    bl_label = ""
    bl_description = ""
    bl_options = {'REGISTER', 'INTERNAL'}

    names: bpy.props.StringProperty(default="")
    label: bpy.props.StringProperty(default="")

    @classmethod
    def description(cls, context, properties):
        if properties.names:
            return f"{properties.label}:\n{properties.names}"
        return properties.label

    def execute(self, context):
        return {'CANCELLED'}  # No-op, tooltip-only

class MISettings(bpy.types.PropertyGroup):
    json_file: bpy.props.StringProperty(
        name="UMAP JSON File",
        description="The level JSON file to import",
        default="",
        subtype="FILE_PATH",
        update=update_analysis,
    )
    base_directory: bpy.props.StringProperty(
        name="Assets Base Directory",
        description="Directory that contains all unpacked assets (e.g., Content folder)",
        default="",
        subtype="DIR_PATH",
        update=update_analysis,
    )
    game_paks_path: bpy.props.StringProperty(
        name="Game Paks Directory",
        description="Path to the game Paks folder (containing .pak / .utoc / .ucas files)",
        default="",
        subtype="DIR_PATH",
    )
    game_aes_key: bpy.props.StringProperty(
        name="Game AES Key",
        description="AES key for decrypting game containers (hex string). Leave empty if the game doesn't use encryption",
        default="",
    )
    game_usmap_path: bpy.props.StringProperty(
        name="Game Mappings (.usmap)",
        description="Optional path to the .usmap mapping file. Required for games with unversioned properties (UE5+). Leave empty if not needed",
        default="",
        subtype="FILE_PATH",
    )
    hide_collisions: bpy.props.BoolProperty(
        name="Hide Collision Meshes",
        description="Automatically hide UCX/UBX/USP physical collision objects on import",
        default=True,
    )
    skip_missing_assets: bpy.props.BoolProperty(
        name="Skip Missing Assets",
        description="Do not create Empty placeholders for missing 3D meshes and clean up empty groups",
        default=False,
    )
    skip_missing_materials: bpy.props.BoolProperty(
        name="Skip Missing Materials",
        description="Do not import objects whose corresponding material JSON files are missing on disk",
        default=False,
    )
    import_decals: bpy.props.BoolProperty(
        name="Import Decals",
        description="Enable or disable importing Unreal decals",
        default=True,
    )
    disable_viewport_refresh: bpy.props.BoolProperty(
        name="Disable Viewport Refresh",
        description="Do not refresh the 3D viewport during import to significantly speed up the process",
        default=True,
    )
    use_smart_resolve: bpy.props.BoolProperty(
        name="Smart Asset Recognition",
        description="Fuzzy matching for asset variants, suffixes and families (e.g. Mi_Decal_Leak02a -> Mi_Decal_Leak02a1, or Mi_Decal_Vertical_A -> Mi_Decal_Vertical_B)",
        default=True,
        update=update_analysis,
    )
    show_analysis: bpy.props.BoolProperty(
        name="Show Asset Analysis",
        description="Check and show a summary of missing assets and folders",
        default=False,
        update=update_analysis,
    )
    analysis_mode: bpy.props.EnumProperty(
        name="Analysis Mode",
        description="Filter displayed folders",
        items=[
            ('MISSING', "Missing Folders", "Show only folders with missing assets"),
            ('ALL', "All Referenced", "Show all folders referenced by the map"),
        ],
        default='MISSING',
        update=update_analysis,
    )
    analysis_depth: bpy.props.IntProperty(
        name="Folder Grouping Depth",
        description="Truncate and group folder paths to a maximum depth (0 to show full parent folders)",
        default=0,
        min=0,
        max=10,
        update=update_analysis,
    )
    analysis_folders: bpy.props.CollectionProperty(type=LIS_ReferencedFolderItem)
    analysis_folders_index: bpy.props.IntProperty(default=0)
    sublevels: bpy.props.CollectionProperty(type=LIS_SubLevelItem)
    sublevels_index: bpy.props.IntProperty(default=0)
    show_sublevels: bpy.props.BoolProperty(
        name="Show Sub-levels",
        description="Show or hide the list of streaming sub-levels",
        default=True,
    )
    last_scanned_json: bpy.props.StringProperty(default="")
    import_progress: bpy.props.FloatProperty(
        name="Import Progress",
        description="Current import progress percentage",
        default=0.0,
        min=0.0,
        max=100.0,
        subtype='PERCENTAGE',
    )
    import_status: bpy.props.StringProperty(
        name="Import Status",
        description="Current step of the import process",
        default="Idle",
    )

class VIEW3D_PT_map_importer_panel(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "UMAP Importer"
    bl_label = "UMAP Importer"

    def draw(self, context):
        scene = context.scene
        mytool = scene.my_tool
        layout = self.layout
        
        # 1. File Selection Section
        box_files = layout.box()
        box_files.label(text="Input Paths", icon="FOLDER_REDIRECT")
        box_files.prop(mytool, "json_file", text="UMAP JSON")
        box_files.prop(mytool, "base_directory", text="Asset Dir")
        
        # 2. Options Grid Section
        box_opts = layout.box()
        box_opts.label(text="Settings", icon="PREFERENCES")
        row = box_opts.row(align=True)
        
        col_left = row.column(align=True)
        col_left.prop(mytool, "disable_viewport_refresh")
        col_left.prop(mytool, "hide_collisions")
        col_left.prop(mytool, "import_decals")
        
        col_right = row.column(align=True)
        col_right.prop(mytool, "use_smart_resolve")
        col_right.prop(mytool, "skip_missing_assets")
        col_right.prop(mytool, "skip_missing_materials")
        
        # 3. Game Asset Extractor Section (temporarily disabled)
        # TODO: re-enable once extraction is validated
        # box_extract = layout.box()
        # box_extract.label(text="Automatic Game Extraction", icon="SCENE_DATA")
        # box_extract.prop(mytool, "game_paks_path", text="Paks Dir")
        # box_extract.prop(mytool, "game_aes_key", text="AES Key (optional)")
        # box_extract.prop(mytool, "game_usmap_path", text="Mappings .usmap (optional)")
        # box_extract.row().operator("lis.auto_extract_assets", text="Extract Missing Assets", icon="IMPORT")
        
        # 4. Import Action Button
        _importing = 0.0 < mytool.import_progress < 100.0
        row_import = layout.row()
        row_import.scale_y = 1.5
        row_import.enabled = not _importing
        row_import.operator(MapImporter.bl_idname, text="Import Level", icon="IMPORT")
        
        # 5. Progress / Status Section
        if mytool.import_progress > 0.0 and mytool.import_progress < 100.0:
            box_prog = layout.box()
            box_prog.label(text=mytool.import_status, icon="INFO")
            row_prog = box_prog.row()
            row_prog.prop(mytool, "import_progress", text="Progress", slider=True)
            row_prog.enabled = False
            box_prog.label(text="Press ESC in 3D View to cancel", icon="CANCEL")
        elif mytool.import_progress == 100.0:
            box_prog = layout.box()
            box_prog.label(text="Import completed successfully!", icon="CHECKMARK")
        
        # 6 & 7. Bottom toggle buttons — side by side to save vertical space
        row_toggles = layout.row(align=True)
        if len(mytool.sublevels) > 0:
            row_toggles.prop(mytool, "show_sublevels",
                             text=f"Sub-levels ({len(mytool.sublevels)})",
                             icon="SCENE_DATA", toggle=True)
        _total = _analysis_results.get("total_count", 0)
        _analysis_label = f"Asset Analysis ({_total})" if _total > 0 else "Asset Analysis"
        row_toggles.prop(mytool, "show_analysis",
                         text=_analysis_label,
                         icon="FILE_TEXT", toggle=True)

        
        # Sub-levels expanded content
        if mytool.show_sublevels and len(mytool.sublevels) > 0:
            box_sub = layout.box()
            box_sub.template_list(
                "LIS_UL_sublevels", "",
                mytool, "sublevels",
                mytool, "sublevels_index",
                rows=4
            )
        
        # Analysis expanded content
        if mytool.show_analysis:
            box_analysis = layout.box()
            box_analysis.label(text=_analysis_results["status"], icon="INFO")
            
            row_ctrl = box_analysis.row(align=True)
            row_ctrl.prop(mytool, "analysis_depth", text="Depth", slider=True)
            row_ctrl.operator("lis.refresh_analysis", text="", icon="FILE_REFRESH")
            
            if _analysis_results["folders"]:
                box_analysis.prop(mytool, "analysis_mode", expand=True)
                
                if len(mytool.analysis_folders) == 0:
                    box_analysis.label(text="No folders found.", icon="CHECKMARK")
                else:
                    box_analysis.template_list(
                        "LIS_UL_referenced_folders", "",
                        mytool, "analysis_folders",
                        mytool, "analysis_folders_index",
                        rows=5
                    )
                    
            box_analysis.operator("lis.export_missing_assets", text="Export Detailed List to Text File", icon="EXPORT")

# -----------------------------------------------------------------------------
# HELPER FUNCTIONS FOR FILE SEARCHING, TRANSFORMS & GEOMETRY
# -----------------------------------------------------------------------------

def is_basic_shape(ue_path):
    """
    Checks if a virtual Unreal Engine path represents a built-in basic shape mesh
    (e.g., /Engine/BasicShapes/Cube).
    """
    if not ue_path:
        return False
    path_lower = ue_path.lower()
    for name in ("cube", "sphere", "cylinder", "cone", "plane"):
        if f"engine/basicshapes/{name}" in path_lower or path_lower.endswith(f"basicshapes/{name}"):
            return True
    return False

def is_basic_shape_material(ue_path):
    """
    Checks if a virtual Unreal Engine path represents the built-in basic shape material
    (e.g., /Engine/BasicShapes/BasicShapeMaterial).
    """
    if not ue_path:
        return False
    path_lower = ue_path.lower()
    return "engine/basicshapes/basicshapematerial" in path_lower or path_lower.endswith("basicshapes/basicshapematerial")

def create_basic_shape(ue_path, name, collection):
    """
    Auto-generates Unreal Engine basic shapes inside Blender using bmesh,
    with proper pivots, scales, and UV coordinates matching Unreal defaults.
    """
    import bmesh
    import math
    
    path_lower = ue_path.lower()
    mesh = bpy.data.meshes.new(name=f"{name}_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    
    bm = bmesh.new()
    
    # 1. Geometry generation (dimensions in meters matching 100cm Unreal default basic shapes)
    if "cube" in path_lower:
        bmesh.ops.create_cube(bm, size=1.0)
    elif "sphere" in path_lower:
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.5)
    elif "cylinder" in path_lower:
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32, radius1=0.5, radius2=0.5, depth=1.0)
    elif "cone" in path_lower:
        # Pivot in Unreal is at the base of the cone, so we translate vertices up by 0.5m along Z
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32, radius1=0.5, radius2=0.0, depth=1.0)
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0.0, 0.0, 0.5))
    elif "plane" in path_lower:
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
    else:
        # Fallback to cube
        bmesh.ops.create_cube(bm, size=1.0)
        
    bm.to_mesh(mesh)
    bm.free()
    
    # 2. UV mapping generation (so materials map correctly)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    
    if "cube" in path_lower:
        # Map each face loops to full 0..1 coordinates
        for face in mesh.polygons:
            uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
            for idx, loop_idx in enumerate(face.loop_indices):
                uv_layer.data[loop_idx].uv = uvs[idx % 4]
    elif "plane" in path_lower:
        # Plane projection: Map vertices from [-0.5, 0.5] range to [0.0, 1.0] UVs
        for loop in mesh.loops:
            vco = mesh.vertices[loop.vertex_index].co
            uv_layer.data[loop.index].uv = (vco.x + 0.5, vco.y + 0.5)
    elif "sphere" in path_lower:
        # Spherical projection
        for loop in mesh.loops:
            vco = mesh.vertices[loop.vertex_index].co.normalized()
            u = 0.5 + math.atan2(vco.y, vco.x) / (2.0 * math.pi)
            v_val = 0.5 + math.asin(vco.z) / math.pi
            uv_layer.data[loop.index].uv = (u, v_val)
    elif "cylinder" in path_lower or "cone" in path_lower:
        # Cylindrical projection for sides, planar for caps
        for face in mesh.polygons:
            is_cap = abs(face.normal.z) > 0.9
            for loop_idx in face.loop_indices:
                vco = mesh.vertices[mesh.loops[loop_idx].vertex_index].co
                if is_cap:
                    uv_layer.data[loop_idx].uv = (vco.x + 0.5, vco.y + 0.5)
                else:
                    u = 0.5 + math.atan2(vco.y, vco.x) / (2.0 * math.pi)
                    v_val = vco.z + 0.5 if "cylinder" in path_lower else vco.z  # Z goes 0 to 1 for cone after translation
                    uv_layer.data[loop_idx].uv = (u, v_val)
                    
    mesh.update()
    
    # 3. Assign default BasicShapeMaterial
    mat_name = "BasicShapeMaterial"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        shader = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if shader:
            shader.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
            shader.inputs["Roughness"].default_value = 0.5
    obj.data.materials.append(mat)
    
    return obj

def is_collision_mesh(name):
    """
    Checks if a given object name corresponds to Unreal physical collision meshes
    (e.g., UCX_*, UBX_*, USP_*, UCP_*, or names with collision).
    """
    name_lower = name.lower()
    return name_lower.startswith(("ucx_", "ubx_", "usp_", "ucp_")) or "collision" in name_lower

def index_directory(base_dir):
    """
    Scans base_dir once and indexes all relevant assets by lowercased names,
    extensions, and suffix paths. This yields O(1) file lookups.
    """
    print(f"Scanning asset directory: {base_dir}")
    index = {}
    valid_exts = {'.gltf', '.glb', '.fbx', '.obj', '.tga', '.png', '.jpg', '.jpeg', '.dds', '.mat', '.json'}
    
    for root, dirs, files in os.walk(base_dir):
        for file in files:
            name, ext = os.path.splitext(file)
            ext_lower = ext.lower()
            if ext_lower not in valid_exts:
                continue
                
            abs_path = os.path.join(root, file)
            rel_path = os.path.relpath(abs_path, base_dir).replace('\\', '/').lower()
            name_lower = name.lower()
            
            # Key 1: Filename without extension
            if name_lower not in index:
                index[name_lower] = []
            index[name_lower].append(abs_path)
            
            # Key 2: Filename with extension
            name_ext = (name + ext_lower).lower()
            if name_ext not in index:
                index[name_ext] = []
            index[name_ext].append(abs_path)
            
            # Key 3: Suffix paths
            parts = rel_path.split('/')
            for i in range(len(parts)):
                suffix_key = '/'.join(parts[i:])
                if suffix_key not in index:
                    index[suffix_key] = []
                index[suffix_key].append(abs_path)
                
                suffix_no_ext = os.path.splitext(suffix_key)[0]
                if suffix_no_ext not in index:
                    index[suffix_no_ext] = []
                index[suffix_no_ext].append(abs_path)
                
    print(f"Scanned {sum(len(v) for v in index.values())} keys in asset index.")
    return index

_resolve_cache = {}
_sorted_keys_cache = {}

def get_sorted_keys(index):
    global _sorted_keys_cache
    index_id = id(index)
    if index_id not in _sorted_keys_cache:
        _sorted_keys_cache[index_id] = sorted([k for k in index.keys() if isinstance(k, str)])
    return _sorted_keys_cache[index_id]

def resolve_file_path(index, ue_path, preferred_exts=None):
    """
    Wrapper around _resolve_file_path_internal that uses a global cache
    to avoid redundant string manipulation and lookups.
    """
    if not ue_path:
        return None
    global _resolve_cache
    ext_key = tuple(sorted(preferred_exts)) if preferred_exts else None
    cache_key = (ue_path, ext_key)
    if cache_key in _resolve_cache:
        return _resolve_cache[cache_key]
    res = _resolve_file_path_internal(index, ue_path, preferred_exts)
    _resolve_cache[cache_key] = res
    return res

def is_safe_fallback(req, cand):
    req_lower = req.lower()
    cand_lower = cand.lower()
    
    # 1. If requested looks like a normal map, candidate must look like a normal map
    is_req_normal = any(x in req_lower for x in ("normal", "_n.", "_n_", "_n'"))
    is_cand_normal = any(x in cand_lower for x in ("normal", "_n.", "_n_", "_n'"))
    if is_req_normal != is_cand_normal:
        return False
        
    # 2. If requested looks like a diffuse/color map, candidate must not look like a normal map
    is_req_diffuse = any(x in req_lower for x in ("diffuse", "basecolor", "albedo", "color", "_d.", "_d_", "_bc."))
    if is_req_diffuse and is_cand_normal:
        return False
        
    # 3. If requested looks like a mask map, candidate must not look like a normal or diffuse map
    is_req_mask = any(x in req_lower for x in ("mask", "orm", "srm", "mra"))
    if is_req_mask and (is_cand_normal or is_req_diffuse):
        return False
        
    return True

def _resolve_file_path_internal(index, ue_path, preferred_exts=None):
    """
    Resolves an Unreal virtual asset path to a physical path on disk using
    the pre-scanned folder index. Supports flexible matching.
    """
    if not ue_path:
        return None
        
    # Clean the Unreal path format (e.g. /Game/Foliage/Tree.1 -> game/foliage/tree)
    clean_path = ue_path.split('.')[0].replace('\\', '/').lower()
    if clean_path.startswith('/'):
        clean_path = clean_path[1:]
    if clean_path.startswith('game/'):
        clean_path = clean_path[5:]
        
    parts = clean_path.split('/')
    
    # Try finding matches by longest matching suffix paths
    for i in range(len(parts)):
        suffix = '/'.join(parts[i:])
        if suffix in index:
            candidates = index[suffix]
            if preferred_exts:
                filtered = [c for c in candidates if os.path.splitext(c)[1].lower() in preferred_exts]
                if filtered:
                    return filtered[0]
            return candidates[0]
            
    # Try fallback to matching the base filename
    filename = parts[-1]
    if filename in index:
        candidates = index[filename]
        if preferred_exts:
            filtered = [c for c in candidates if os.path.splitext(c)[1].lower() in preferred_exts]
            if filtered:
                return filtered[0]
        return candidates[0]
        
    # Check settings for smart resolution
    use_smart = True
    try:
        use_smart = bpy.context.scene.my_tool.use_smart_resolve
    except Exception:
        pass

    if not use_smart:
        return None

    # --- Loose / Variant Fallback Matching ---
    import re
    
    # Generate potential fallback base names in order of specificity.
    fallback_bases = []
    
    # 1. Prefix match (requested is prefix of existing, e.g. "mi_decal_leak02a" -> "mi_decal_leak02a1")
    fallback_bases.append((filename, "prefix"))
    
    # 2. Strip trailing digits (e.g. "mi_decal_leak02a1" -> "mi_decal_leak02a")
    base_no_digits = re.sub(r'\d+$', '', filename)
    if base_no_digits and base_no_digits != filename and len(base_no_digits) >= 4:
        fallback_bases.append((base_no_digits, "base without digits"))
        
    # 3. Strip underscore variant (e.g. "mi_decal_vertical_a" -> "mi_decal_vertical")
    base_no_under = re.sub(r'_[a-z\d]$', '', filename)
    if base_no_under and base_no_under != filename and len(base_no_under) >= 4:
        fallback_bases.append((base_no_under, "base without underscore variant"))
        
    # 4. Strip single trailing character (e.g. "mi_decal_leak02a" -> "mi_decal_leak02")
    base_no_char = re.sub(r'[a-z\d]$', '', filename)
    if base_no_char and base_no_char != filename and len(base_no_char) >= 4:
        fallback_bases.append((base_no_char, "base without trailing char"))

    import bisect
    sorted_keys = get_sorted_keys(index)

    # Try each fallback base name in order
    for base, desc in fallback_bases:
        candidates = []
        # Binary search for the first key starting with or greater than base
        idx = bisect.bisect_left(sorted_keys, base)
        while idx < len(sorted_keys):
            key = sorted_keys[idx]
            if key.startswith(base):
                candidates.extend(index.get(key, []))
                idx += 1
            else:
                break
                
        if candidates:
            # Filter candidates using is_safe_fallback to avoid cross-type mismatches
            safe_candidates = [c for c in candidates if is_safe_fallback(filename, os.path.basename(c))]
            if safe_candidates:
                safe_candidates.sort()  # Sort alphabetically to be deterministic and pick the lowest/first variant
                if preferred_exts:
                    filtered = [c for c in safe_candidates if os.path.splitext(c)[1].lower() in preferred_exts]
                    if filtered:
                        print(f"Warning: Exact asset '{ue_path}' not found. Using smart fallback ({desc}): '{os.path.basename(filtered[0])}'")
                        return filtered[0]
                fallback_path = safe_candidates[0]
                print(f"Warning: Exact asset '{ue_path}' not found. Using smart fallback ({desc}): '{os.path.basename(fallback_path)}'")
                return fallback_path

    return None


_blueprint_mesh_cache = {}
_blueprint_properties_cache = {}
_blueprint_object_cache = {}
_parsed_bp_cache = {}

def resolve_blueprint_object(index, template_path):
    if not template_path:
        return {}
    global _blueprint_object_cache, _parsed_bp_cache
    if template_path in _blueprint_object_cache:
        return _blueprint_object_cache[template_path]
    parts = template_path.split('.')
    bp_path = parts[0]
    try:
        suffix_idx = int(parts[-1])
    except ValueError:
        suffix_idx = None
    bp_json_path = resolve_file_path(index, bp_path, preferred_exts={'.json'})
    if not bp_json_path or not os.path.exists(bp_json_path):
        _blueprint_object_cache[template_path] = {}
        return {}
    try:
        if bp_json_path in _parsed_bp_cache:
            bp_data, bp_by_name = _parsed_bp_cache[bp_json_path]
        else:
            with open(bp_json_path, 'r', encoding='utf-8') as f:
                bp_data = json.load(f)
            if not isinstance(bp_data, list):
                bp_data = [bp_data]
            bp_by_name = {}
            for item in bp_data:
                if isinstance(item, dict):
                    name = item.get("Name")
                    if name:
                        bp_by_name[name] = item
            _parsed_bp_cache[bp_json_path] = (bp_data, bp_by_name)

        obj = None
        if suffix_idx is not None and suffix_idx < len(bp_data):
            obj = bp_data[suffix_idx]
        else:
            obj_name = template_path.split(':')[-1] if ':' in template_path else parts[-1]
            obj = bp_by_name.get(obj_name)
            if not obj:
                # Substring fallback scan
                for item in bp_data:
                    if isinstance(item, dict) and obj_name in item.get("Name", ""):
                        obj = item
                        break
        if obj and isinstance(obj, dict):
            _blueprint_object_cache[template_path] = obj
            return obj
    except Exception as e:
        print(f"Error parsing blueprint template object for {bp_json_path}: {e}")
    _blueprint_object_cache[template_path] = {}
    return {}

def resolve_blueprint_properties(index, template_path):
    """
    Loads and parses a blueprint class template JSON to find the properties
    of the component corresponding to template_path.
    """
    if not template_path:
        return {}
    global _blueprint_properties_cache
    if template_path in _blueprint_properties_cache:
        return _blueprint_properties_cache[template_path]
    obj = resolve_blueprint_object(index, template_path)
    if obj:
        props = obj.get("Properties", {})
        _blueprint_properties_cache[template_path] = props
        return props
    _blueprint_properties_cache[template_path] = {}
    return {}

def resolve_blueprint_mesh(index, template_path):
    """
    Loads and parses a blueprint class template JSON to find the default
    StaticMesh or SkeletalMesh path if it is null or missing in the UMAP level JSON.
    """
    if not template_path:
        return None
    global _blueprint_mesh_cache, _parsed_bp_cache
    if template_path in _blueprint_mesh_cache:
        return _blueprint_mesh_cache[template_path]
    obj = resolve_blueprint_object(index, template_path)
    if obj:
        props = obj.get("Properties", {})
        sm_ref = props.get("StaticMesh") or props.get("SkeletalMesh") or obj.get("StaticMesh") or obj.get("SkeletalMesh")
        if sm_ref and isinstance(sm_ref, dict):
            res = sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
            _blueprint_mesh_cache[template_path] = res
            return res
    parts = template_path.split('.')
    bp_path = parts[0]
    bp_json_path = resolve_file_path(index, bp_path, preferred_exts={'.json'})
    if bp_json_path and os.path.exists(bp_json_path):
        try:
            if bp_json_path in _parsed_bp_cache:
                bp_data, _ = _parsed_bp_cache[bp_json_path]
            else:
                with open(bp_json_path, 'r', encoding='utf-8') as f:
                    bp_data = json.load(f)
                if not isinstance(bp_data, list):
                    bp_data = [bp_data]
                bp_by_name = {}
                for item in bp_data:
                    if isinstance(item, dict):
                        name = item.get("Name")
                        if name:
                            bp_by_name[name] = item
                _parsed_bp_cache[bp_json_path] = (bp_data, bp_by_name)
            for item in bp_data:
                if isinstance(item, dict):
                    p = item.get("Properties", {})
                    sm_ref = p.get("StaticMesh") or p.get("SkeletalMesh") or item.get("StaticMesh") or item.get("SkeletalMesh")
                    if sm_ref and isinstance(sm_ref, dict):
                        res = sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
                        _blueprint_mesh_cache[template_path] = res
                        return res
        except Exception:
            pass
    _blueprint_mesh_cache[template_path] = None
    return None

def should_skip_component_due_to_missing_materials(etype, props, file_index):
    """
    Checks if overridden materials for a mesh or decal component are missing from disk.
    Returns True if skip_missing_materials is enabled and we should skip importing this component.
    """
    mats_to_check = []
    if etype in ("StaticMeshComponent", "InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent", "SkeletalMeshComponent"):
        override_mats = props.get("OverrideMaterials", [])
        for mat in override_mats:
            if mat:
                mat_path = mat.get("ObjectPath") or mat.get("ObjectName")
                if mat_path:
                    mat_name = clean_unreal_path(mat_path)
                    if mat_name:
                        mats_to_check.append(mat_name)
    elif etype == "DecalComponent":
        mat_ref = props.get("DecalMaterial")
        if mat_ref:
            mat_path = mat_ref.get("ObjectPath") or mat_ref.get("ObjectName")
            if mat_path:
                mat_name = clean_unreal_path(mat_path)
                if mat_name:
                    mats_to_check.append(mat_name)
                    
    # If overridden materials are referenced, check if any of them exist on disk
    if mats_to_check:
        any_found = False
        for mat_name in mats_to_check:
            if resolve_file_path(file_index, mat_name, preferred_exts={'.json', '.mat'}):
                any_found = True
                break
        if not any_found:
            return True
            
    return False

def is_material_transparent(mat_name, mat_data):
    """
    Checks if a material should be translucent or masked based on its name and properties.
    """
    # 1. Check material name keywords
    name_lower = mat_name.lower()
    transparent_keywords = {"glass", "translucent", "water", "decal", "window", "fence", "grate", "leaves", "foliage", "hair", "alpha"}
    for kw in transparent_keywords:
        if kw in name_lower:
            return True
            
    # 2. Check JSON data for BlendMode properties
    def check_dict_for_transparency(d):
        if not isinstance(d, dict):
            return False
        for k, v in d.items():
            if k == "BlendMode":
                v_str = str(v).lower()
                if "masked" in v_str or "translucent" in v_str or "additive" in v_str:
                    return True
            elif k == "bIsMasked" and v is True:
                return True
            elif isinstance(v, (dict, list)):
                if check_dict_for_transparency(v):
                    return True
        return False
        
    def check_list_for_transparency(lst):
        if not isinstance(lst, list):
            return False
        for item in lst:
            if isinstance(item, dict):
                if check_dict_for_transparency(item):
                    return True
            elif isinstance(item, list):
                if check_list_for_transparency(item):
                    return True
        return False

    if mat_data:
        if isinstance(mat_data, dict):
            if check_dict_for_transparency(mat_data):
                return True
        elif isinstance(mat_data, list):
            if check_list_for_transparency(mat_data):
                return True
                
    return False

# -----------------------------------------------------------------------------
# GLOBAL CACHE & STATE FOR ASSET ANALYSIS
# -----------------------------------------------------------------------------
_index_cache = {}  # maps normalized base_dir -> (mtime, index_dict)
_analysis_results = {
    "status": "Select a valid UMAP JSON file",
    "missing_count": 0,
    "total_count": 0,
    "folders": []  # list of dicts: {"path": str, "missing": int, "total": int}
}

def get_cached_index(base_dir):
    """
    Retrieves or builds the directory index. Caches the index to prevent rebuilding
    unless the directory's modification time changes.
    """
    if not base_dir or not os.path.exists(base_dir):
        return None
    norm_dir = os.path.abspath(base_dir).lower()
    try:
        mtime = os.path.getmtime(norm_dir)
    except Exception:
        mtime = 0
        
    global _index_cache
    cache_entry = _index_cache.get(norm_dir)
    if cache_entry and cache_entry[0] == mtime:
        return cache_entry[1]
        
    index = index_directory(base_dir)
    _index_cache[norm_dir] = (mtime, index)
    return index

def clean_unreal_path(raw_path):
    """
    Extracts the virtual Unreal asset path (e.g. /Game/Folders/Asset) and removes type wrappers.
    """
    if not raw_path:
        return ""
    path = raw_path
    if "'" in path:
        parts = path.split("'")
        if len(parts) > 1:
            path = parts[1]
    if '"' in path:
        path = path.replace('"', '')
    return path.split('.')[0]

def get_package_path(json_file_path, base_directory):
    try:
        rel = os.path.relpath(json_file_path, base_directory).replace('\\', '/')
        rel_no_ext = os.path.splitext(rel)[0]
        if not rel_no_ext.startswith('/'):
            package = '/Game/' + rel_no_ext
        else:
            package = rel_no_ext
        return package.lower()
    except Exception:
        name = os.path.splitext(os.path.basename(json_file_path))[0]
        return f"/game/map/{name.lower()}/{name.lower()}"

def parse_reference_key(ref, current_package):
    if not ref:
        return None
    path = ref.get("ObjectPath") or ref.get("ObjectName") or ""
    if not path:
        return None
    if "'" in path:
        path = path.split("'")[1]
    parts = path.split('.')
    if len(parts) < 2:
        return None
    try:
        idx = int(parts[-1])
        package = parts[0].lower()
        return (package, idx)
    except ValueError:
        return None

def get_all_referenced_assets(json_filepath, base_dir=None, file_index=None, disabled_sublevels=None):
    """
    Scans a level JSON and all resolved Blueprint templates to extract all
    referenced meshes and materials.
    """
    referenced = set()
    loaded_files = set()
    
    def scan_file(filepath):
        if not filepath or not os.path.exists(filepath):
            return
        abs_path = os.path.abspath(filepath).lower()
        if abs_path in loaded_files:
            return
        loaded_files.add(abs_path)
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            if not isinstance(json_data, list):
                json_data = [json_data]
                
            for entity in json_data:
                props = entity.get("Properties", {})
                etype = entity.get("Type", "")
                
                # 1. Mesh components
                if etype in ("StaticMeshComponent", "InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent", "SkeletalMeshComponent"):
                    sm_ref = props.get("StaticMesh") or props.get("SkeletalMesh")
                    mesh_path = None
                    if sm_ref:
                        mesh_path = sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
                    else:
                        template_ref = entity.get("Template")
                        if template_ref:
                            template_path = template_ref.get("ObjectPath")
                            if template_path and file_index:
                                mesh_path = resolve_blueprint_mesh(file_index, template_path)
                    if mesh_path:
                        referenced.add(("mesh", mesh_path))
                        
                    # Override materials
                    override_mats = props.get("OverrideMaterials", [])
                    for mat in override_mats:
                        if mat:
                            mat_path = mat.get("ObjectPath") or mat.get("ObjectName")
                            if mat_path:
                                referenced.add(("material", mat_path))
                                
                # 2. Decals
                elif etype == "DecalComponent":
                    mat_ref = props.get("DecalMaterial")
                    if mat_ref:
                        mat_path = mat_ref.get("ObjectPath") or mat_ref.get("ObjectName")
                        if mat_path:
                            referenced.add(("material", mat_path))
                            
                # 3. Level Streaming (AlwaysLoaded or dynamic)
                elif "streaming" in etype.lower():
                    world_asset = props.get("WorldAsset")
                    if world_asset:
                        asset_path = world_asset.get("AssetPathName")
                        if asset_path and file_index:
                            clean_p = clean_unreal_path(asset_path)
                            if disabled_sublevels and clean_p.lower() in disabled_sublevels:
                                continue
                            sub_path = resolve_file_path(file_index, clean_p, preferred_exts={'.json'})
                            if sub_path:
                                scan_file(sub_path)
                            else:
                                referenced.add(("level", asset_path))
                                
        except Exception as e:
            print(f"Error gathering referenced assets from {filepath}: {e}")

    scan_file(json_filepath)
    return referenced

def populate_sublevels(mytool):
    if not mytool.json_file or not os.path.exists(mytool.json_file):
        mytool.sublevels.clear()
        mytool.last_scanned_json = ""
        return
        
    if mytool.json_file == mytool.last_scanned_json and len(mytool.sublevels) > 0:
        return
        
    mytool.sublevels.clear()
    mytool.last_scanned_json = mytool.json_file
    
    try:
        with open(mytool.json_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        if not isinstance(json_data, list):
            json_data = [json_data]
            
        base_dir = mytool.base_directory
        file_index = get_cached_index(base_dir) if (base_dir and os.path.exists(base_dir)) else None

        found = set()
        for entity in json_data:
            etype = entity.get("Type", "")
            if "streaming" in etype.lower():
                props = entity.get("Properties", {})
                world_asset = props.get("WorldAsset")
                if world_asset:
                    asset_path = world_asset.get("AssetPathName")
                    if asset_path:
                        clean_p = clean_unreal_path(asset_path)
                        name = os.path.basename(clean_p)
                        if clean_p not in found:
                            found.add(clean_p)
                            item = mytool.sublevels.add()
                            item.name = name
                            item.package_path = clean_p.lower()
                            item.enabled = True
                            
                            is_found = False
                            if file_index:
                                is_found = bool(resolve_file_path(file_index, clean_p.lower(), preferred_exts={'.json'}))
                            item.found = is_found
    except Exception as e:
        print(f"Error scanning sublevels for UI: {e}")

def run_asset_analysis(mytool):
    """
    Processes all level JSON assets, verifies existence, and groups missing/total counts by folder.
    """
    populate_sublevels(mytool)
    
    global _analysis_results, _resolve_cache, _blueprint_object_cache, _blueprint_mesh_cache, _blueprint_properties_cache, _parsed_bp_cache
    _resolve_cache.clear()
    _blueprint_object_cache.clear()
    _blueprint_mesh_cache.clear()
    _blueprint_properties_cache.clear()
    _parsed_bp_cache.clear()
    json_path = mytool.json_file
    base_dir = mytool.base_directory
    
    if not json_path or not os.path.exists(json_path):
        _analysis_results = {
            "status": "Select a valid UMAP JSON file",
            "missing_count": 0,
            "total_count": 0,
            "folders": []
        }
        return
        
    file_index = None
    if base_dir and os.path.exists(base_dir):
        file_index = get_cached_index(base_dir)
        
    for item in mytool.sublevels:
        is_found = False
        if file_index:
            is_found = bool(resolve_file_path(file_index, item.package_path, preferred_exts={'.json'}))
        item.found = is_found
        
    disabled_sublevels = {item.package_path for item in mytool.sublevels if not item.enabled}
    referenced_assets = get_all_referenced_assets(json_path, base_dir, file_index, disabled_sublevels)
    if not referenced_assets:
        _analysis_results = {
            "status": "No assets referenced or JSON empty/invalid",
            "missing_count": 0,
            "total_count": 0,
            "folders": []
        }
        return
        
    folders_data = {}
    missing_count = 0
    
    depth = mytool.analysis_depth
    
    for asset_type, ue_path in referenced_assets:
        clean_path = clean_unreal_path(ue_path)
        if not clean_path:
            continue
            
        parts = [p for p in clean_path.split('/') if p]
        if depth <= 0:
            if len(parts) > 1:
                folder = '/' + '/'.join(parts[:-1])
            else:
                folder = "/Game"
        else:
            if len(parts) <= depth:
                if len(parts) > 1:
                    folder = '/' + '/'.join(parts[:-1])
                else:
                    folder = '/' + '/'.join(parts)
            else:
                folder = '/' + '/'.join(parts[:depth])
            
        if folder not in folders_data:
            folders_data[folder] = {"missing": 0, "missing_meshes": 0, "missing_materials": 0, "missing_levels": 0,
                                    "missing_mesh_names": [], "missing_material_names": [], "missing_level_names": [], "total": 0}
            
        folders_data[folder]["total"] += 1
        
        found = False
        if asset_type == "mesh" and is_basic_shape(ue_path):
            found = True
        elif asset_type == "material" and is_basic_shape_material(ue_path):
            found = True
        elif file_index:
            if asset_type == "mesh":
                preferred = {'.gltf', '.glb', '.fbx', '.obj'}
            elif asset_type == "level":
                preferred = {'.json'}
            else:
                preferred = {'.json', '.mat'}
            found_path = resolve_file_path(file_index, clean_path, preferred_exts=preferred)
            if found_path:
                found = True
                
        if not found:
            folders_data[folder]["missing"] += 1
            asset_name = parts[-1] if parts else clean_path
            if asset_type == "mesh":
                folders_data[folder]["missing_meshes"] += 1
                folders_data[folder]["missing_mesh_names"].append(asset_name)
            elif asset_type == "level":
                folders_data[folder]["missing_levels"] += 1
                folders_data[folder]["missing_level_names"].append(asset_name)
            else:
                folders_data[folder]["missing_materials"] += 1
                folders_data[folder]["missing_material_names"].append(asset_name)
            missing_count += 1
            
    folders_list = []
    for f_path, data in folders_data.items():
        # Cap names at 15 entries for tooltip readability
        mesh_names = data["missing_mesh_names"][:15]
        mat_names = data["missing_material_names"][:15]
        level_names = data["missing_level_names"][:15]
        if len(data["missing_mesh_names"]) > 15:
            mesh_names.append(f"... and {len(data['missing_mesh_names']) - 15} more")
        if len(data["missing_material_names"]) > 15:
            mat_names.append(f"... and {len(data['missing_material_names']) - 15} more")
        if len(data["missing_level_names"]) > 15:
            level_names.append(f"... and {len(data['missing_level_names']) - 15} more")
        folders_list.append({
            "path": f_path,
            "missing": data["missing"],
            "missing_meshes": data["missing_meshes"],
            "missing_materials": data["missing_materials"],
            "missing_levels": data["missing_levels"],
            "missing_mesh_names": "\n".join(mesh_names),
            "missing_material_names": "\n".join(mat_names),
            "missing_level_names": "\n".join(level_names),
            "total": data["total"]
        })
        
    # Sort folders based on display mode
    if mytool.analysis_mode == 'ALL':
        folders_list.sort(key=lambda x: x["path"].lower())
    else:
        folders_list.sort(key=lambda x: x["missing"], reverse=True)
    
    has_dir = bool(base_dir and os.path.exists(base_dir))
    if has_dir:
        status_str = f"Found: {len(referenced_assets) - missing_count} / {len(referenced_assets)} assets ({missing_count} missing)."
    else:
        status_str = f"Total: {len(referenced_assets)} referenced assets. Select directory to verify."
        
    _analysis_results = {
        "status": status_str,
        "missing_count": missing_count,
        "total_count": len(referenced_assets),
        "folders": folders_list
    }
    
    # Populate the Blender PropertyGroup collection for template_list scrollbar view
    mytool.analysis_folders.clear()
    show_all = mytool.analysis_mode == 'ALL'
    filtered_folders = [f for f in folders_list if show_all or f["missing"] > 0]
    
    for f in filtered_folders:
        item = mytool.analysis_folders.add()
        item.path = f["path"]
        item.missing = f["missing"]
        item.missing_meshes = f["missing_meshes"]
        item.missing_materials = f["missing_materials"]
        item.missing_levels = f["missing_levels"]
        item.missing_mesh_names = f["missing_mesh_names"]
        item.missing_material_names = f["missing_material_names"]
        item.missing_level_names = f["missing_level_names"]
        item.total = f["total"]


def get_blender_transform(loc_dict, rot_dict, scale_dict):
    """
    Converts Unreal Engine local coordinates (LHS, centimeters, Euler/Quaternion)
    into a Blender 4x4 transform Matrix (RHS, meters).
    """
    # Location (divide by 100 to convert cm to meters, Y is negated)
    tx = loc_dict.get("X", 0.0)
    ty = loc_dict.get("Y", 0.0)
    tz = loc_dict.get("Z", 0.0)
    loc = mathutils.Vector((tx / 100.0, -ty / 100.0, tz / 100.0))
    
    # Scale
    sx = scale_dict.get("X", 1.0)
    sy = scale_dict.get("Y", 1.0)
    sz = scale_dict.get("Z", 1.0)
    scale = mathutils.Vector((sx, sy, sz))
    
    # Rotation (handle Quaternion and Euler representations)
    if "W" in rot_dict:
        qw = rot_dict.get("W", 1.0)
        qx = rot_dict.get("X", 0.0)
        qy = rot_dict.get("Y", 0.0)
        qz = rot_dict.get("Z", 0.0)
        q = mathutils.Quaternion((qw, qx, qy, qz))
        rot_mat_ue = q.to_matrix().to_4x4()
    else:
        pitch = rot_dict.get("Pitch", 0.0)
        yaw = rot_dict.get("Yaw", 0.0)
        roll = rot_dict.get("Roll", 0.0)
        # Unreal applies Roll -> Pitch -> Yaw (X -> Y -> Z) order
        euler_ue = mathutils.Euler((math.radians(roll), math.radians(pitch), math.radians(yaw)), 'XYZ')
        rot_mat_ue = euler_ue.to_matrix().to_4x4()
        
    # Change basis for left-to-right handed conversion: C = diag(1, -1, 1)
    C = mathutils.Matrix.Diagonal((1, -1, 1)).to_4x4()
    rot_mat_blender = C @ rot_mat_ue @ C
    
    loc_mat = mathutils.Matrix.Translation(loc)
    scale_mat = mathutils.Matrix.Diagonal(scale).to_4x4()
    
    return loc_mat @ rot_mat_blender @ scale_mat

def clone_hierarchy(src_obj, name, collection, unhide=True, hide_collisions=False):
    """
    Creates a deep copy of a Blender object hierarchy, sharing the original
    mesh data blocks to preserve memory and keep things instanced.
    """
    if not src_obj.children:
        new_obj = bpy.data.objects.new(name=name, object_data=src_obj.data)
        collection.objects.link(new_obj)
        new_obj.matrix_basis = src_obj.matrix_basis.copy()
        if hide_collisions and is_collision_mesh(src_obj.name):
            new_obj.hide_viewport = True
            new_obj.hide_render = True
        elif unhide:
            new_obj.hide_viewport = False
            new_obj.hide_render = False
        else:
            new_obj.hide_viewport = src_obj.hide_viewport
            new_obj.hide_render = src_obj.hide_render
        return new_obj

    lookup = {}
    
    def clone_node(obj):
        new_obj = bpy.data.objects.new(name=f"{obj.name}_Instance", object_data=obj.data)
        collection.objects.link(new_obj)
        new_obj.matrix_basis = obj.matrix_basis.copy()
        
        # Enforce collision mesh visibility rules
        if hide_collisions and is_collision_mesh(obj.name):
            new_obj.hide_viewport = True
            new_obj.hide_render = True
        elif unhide:
            new_obj.hide_viewport = False
            new_obj.hide_render = False
        else:
            new_obj.hide_viewport = obj.hide_viewport
            new_obj.hide_render = obj.hide_render
        
        lookup[obj] = new_obj
        for child in obj.children:
            new_child = clone_node(child)
            new_child.parent = new_obj
            
        return new_obj
        
    root_clone = clone_node(src_obj)
    root_clone.name = name
    return root_clone

def import_asset(filepath, name, collection, hide_collisions=False):
    """
    Imports a 3D model asset (GLTF, GLB, FBX, or OBJ) into Blender,
    unlinks it from default collections, and links it to the active map collection.
    """
    existing_objects = set(bpy.data.objects)
    existing_collections = set(bpy.data.collections)
    ext = os.path.splitext(filepath)[1].lower()
    
    try:
        if ext in ('.gltf', '.glb'):
            if hasattr(bpy.ops.wm, "gltf_import") and "gltf_import" in dir(bpy.ops.wm):
                bpy.ops.wm.gltf_import(filepath=filepath, loglevel=50)
            else:
                bpy.ops.import_scene.gltf(filepath=filepath, loglevel=50)
        elif ext == '.fbx':
            bpy.ops.import_scene.fbx(filepath=filepath)
        elif ext == '.obj':
            if hasattr(bpy.ops.wm, "obj_import") and "obj_import" in dir(bpy.ops.wm):
                bpy.ops.wm.obj_import(filepath=filepath)
            else:
                bpy.ops.import_scene.obj(filepath=filepath)
        else:
            return None
    except Exception as e:
        print(f"Error importing asset file {filepath}: {e}")
        return None
        
    new_objs = [obj for obj in bpy.data.objects if obj not in existing_objects]
    if not new_objs:
        return None
        
    roots = [obj for obj in new_objs if obj.parent not in new_objs]
    
    # Organize in collections and hide collisions if enabled
    for obj in new_objs:
        # Unlink from all other collections
        for col in list(obj.users_collection):
            if col != collection:
                try:
                    col.objects.unlink(obj)
                except Exception:
                    pass
        # Link to our target collection
        if obj.name not in collection.objects:
            collection.objects.link(obj)
            
        if hide_collisions and is_collision_mesh(obj.name):
            obj.hide_viewport = True
            obj.hide_render = True
            
    # Clean up empty collections created by the importer
    new_collections = [col for col in bpy.data.collections if col not in existing_collections]
    for col in new_collections:
        # Unlink from all parent collections
        for parent_col in list(bpy.data.collections):
            if col.name in parent_col.children:
                try:
                    parent_col.children.unlink(col)
                except Exception:
                    pass
        if col.name in bpy.context.scene.collection.children:
            try:
                bpy.context.scene.collection.children.unlink(col)
            except Exception:
                pass
        try:
            bpy.data.collections.remove(col)
        except Exception:
            pass
            
    # Always wrap imported asset roots under a parent Empty to protect orientation transforms
    empty_root = bpy.data.objects.new(name=name, object_data=None)
    collection.objects.link(empty_root)
    for r in roots:
        r.parent = empty_root
    return empty_root

def create_decal_plane_mesh(name, material, collection):
    """
    Generates a quad mesh plane representing the Decal projection area,
    wound to face -X (away from the wall) and offset by -0.1cm to avoid Z-fighting.
    Its base size corresponds to 128x128cm (0.64m half-extent), matching the
    default Unreal Decal size.
    """
    mesh_data = bpy.data.meshes.new(name=f"{name}_Mesh")
    
    # Vertices of the YZ plane facing -X (normal), offset by -0.001m (-0.1cm) to prevent Z-fighting.
    # Half-size is 0.64m (total 1.28m, matching 128cm default Unreal decal size).
    verts = [
        (-0.001, -0.64, -0.64), # Bottom-Right (looking at -X)
        (-0.001, -0.64,  0.64), # Top-Right
        (-0.001,  0.64,  0.64), # Top-Left
        (-0.001,  0.64, -0.64)  # Bottom-Left
    ]
    faces = [(0, 1, 2, 3)]
    
    mesh_data.from_pydata(verts, [], faces)
    mesh_data.update()
    
    # Add UV coordinates to prevent horizontal mirroring
    mesh_data.uv_layers.new(name="UVMap")
    uv_layer = mesh_data.uv_layers.active.data
    uv_layer[0].uv = (1.0, 0.0) # Bottom-Right
    uv_layer[1].uv = (1.0, 1.0) # Top-Right
    uv_layer[2].uv = (0.0, 1.0) # Top-Left
    uv_layer[3].uv = (0.0, 0.0) # Bottom-Left
    
    obj = bpy.data.objects.new(name=name, object_data=mesh_data)
    collection.objects.link(obj)
    
    # Assign material
    if material:
        obj.data.materials.append(material)
        
    return obj

# -----------------------------------------------------------------------------
# OPERATORS
# -----------------------------------------------------------------------------

class MapImporter(bpy.types.Operator):
    bl_idname = "lis.map_import"
    bl_label = "Import Level"
    bl_options = {'REGISTER', 'UNDO'}
    
    _timer = None
    generator = None

    def execute(self, context):
        import gc
        self.gc_was_enabled = gc.isenabled()
        if self.gc_was_enabled:
            gc.disable()

        # Save global undo state and disable it to speed up imports
        self.undo_state = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False

        if bpy.app.background:
            # Headless fallback: run synchronously
            print("Running in background/headless mode. Running synchronously...")
            gen = self.import_generator(context)
            try:
                while not next(gen):
                    pass
            except StopIteration:
                pass
            if self.gc_was_enabled:
                gc.enable()
                gc.collect()
            context.preferences.edit.use_global_undo = self.undo_state
            return {'FINISHED'}
            
        # Interactive mode: run modally with a timer
        scene = context.scene
        mytool = scene.my_tool
        mytool.import_progress = 0.0
        mytool.import_status = "Starting..."
        
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.001, window=context.window)
        wm.modal_handler_add(self)
        
        json_filename = os.path.basename(mytool.json_file)
        self.collection_name = os.path.splitext(json_filename)[0]
        
        self.generator = self.import_generator(context)
        self.last_redraw_time = 0.0
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        scene = context.scene
        mytool = scene.my_tool
        
        if event.type == 'ESC':
            self.report_cleanup(context)
            self.report({'INFO'}, "Import cancelled.")
            return {'CANCELLED'}
            
        if event.type == 'TIMER':
            try:
                finished = next(self.generator)
                
                # Check redraw rate limit
                should_redraw = True
                if mytool.disable_viewport_refresh:
                    current_time = time.time()
                    if current_time - self.last_redraw_time < 1.0 and not finished:
                        should_redraw = False
                
                if should_redraw:
                    self.last_redraw_time = time.time()
                    for window in context.window_manager.windows:
                        for area in window.screen.areas:
                            area.tag_redraw()
                        
                if finished:
                    self.report_cleanup(context)
                    self.report({'INFO'}, "Import finished successfully.")
                    return {'FINISHED'}
            except StopIteration:
                self.report_cleanup(context)
                return {'FINISHED'}
            except Exception as e:
                self.report_cleanup(context)
                self.report({'ERROR'}, f"Import failed: {e}")
                import traceback
                traceback.print_exc()
                return {'CANCELLED'}
                
        return {'PASS_THROUGH'}

    def report_cleanup(self, context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None
        self.generator = None
        
        # Restore global undo preference
        if hasattr(self, 'undo_state'):
            context.preferences.edit.use_global_undo = self.undo_state
            
        # Restore viewport visibility of the collection if it was hidden
        if hasattr(self, 'collection_name') and self.collection_name:
            import_collection = bpy.data.collections.get(self.collection_name)
            if import_collection:
                import_collection.hide_viewport = False
                
        # Re-enable garbage collection if it was disabled
        if hasattr(self, 'gc_was_enabled') and self.gc_was_enabled:
            import gc
            if not gc.isenabled():
                gc.enable()
                gc.collect()
                
        # Clear temporary caches to free memory
        global _blueprint_mesh_cache, _blueprint_properties_cache, _resolve_cache, _blueprint_object_cache, _parsed_bp_cache
        _blueprint_mesh_cache.clear()
        _blueprint_properties_cache.clear()
        _resolve_cache.clear()
        _blueprint_object_cache.clear()
        _parsed_bp_cache.clear()
        
        # Reset progress values
        context.scene.my_tool.import_progress = 0.0
        context.scene.my_tool.import_status = "Idle"

    def import_generator(self, context):
        global _blueprint_mesh_cache, _blueprint_properties_cache, _resolve_cache, _blueprint_object_cache, _parsed_bp_cache
        _blueprint_mesh_cache.clear()
        _blueprint_properties_cache.clear()
        _resolve_cache.clear()
        _blueprint_object_cache.clear()
        _parsed_bp_cache.clear()

        scene = context.scene
        mytool = scene.my_tool
        
        base_dir = mytool.base_directory
        map_json = mytool.json_file
        hide_collisions = mytool.hide_collisions
        skip_missing_assets = mytool.skip_missing_assets
        skip_missing_materials = mytool.skip_missing_materials
        import_decals = mytool.import_decals
        
        mytool.import_status = "Scanning asset directory..."
        mytool.import_progress = 5.0
        yield False
        
        file_index = get_cached_index(base_dir)
        mytool.import_progress = 10.0
        yield False
        
        mytool.import_status = "Parsing level JSON and sub-levels..."
        yield False
        
        disabled_sublevels = {item.package_path for item in mytool.sublevels if not item.enabled}
        
        queue = [map_json]
        loaded_json_paths = {os.path.abspath(map_json).lower()}
        all_entities = []
        missing_sublevels = []
        
        while queue:
            current_json = queue.pop(0)
            current_package = get_package_path(current_json, base_dir)
            try:
                with open(current_json, 'r', encoding='utf-8') as f:
                    json_data = json.load(f)
                if not isinstance(json_data, list):
                    json_data = [json_data]
                for idx, entity in enumerate(json_data):
                    all_entities.append((current_package, idx, entity))
                    
                    etype = entity.get("Type", "")
                    if "streaming" in etype.lower():
                        props = entity.get("Properties", {})
                        world_asset = props.get("WorldAsset")
                        if world_asset:
                            asset_path = world_asset.get("AssetPathName")
                            if asset_path:
                                clean_p = clean_unreal_path(asset_path)
                                if clean_p.lower() in disabled_sublevels:
                                    continue
                                sub_json_path = resolve_file_path(file_index, clean_p, preferred_exts={'.json'})
                                if sub_json_path:
                                    abs_sub = os.path.abspath(sub_json_path).lower()
                                    if abs_sub not in loaded_json_paths:
                                        loaded_json_paths.add(abs_sub)
                                        queue.append(sub_json_path)
                                else:
                                    missing_sublevels.append(asset_path)
            except Exception as e:
                print(f"Error loading level JSON {current_json}: {e}")
                
        total_objects = len(all_entities)
        
        json_filename = os.path.basename(map_json)
        collection_name = os.path.splitext(json_filename)[0]
        import_collection = bpy.data.collections.get(collection_name)
        if not import_collection:
            import_collection = bpy.data.collections.new(collection_name)
            bpy.context.scene.collection.children.link(import_collection)
            
        if mytool.disable_viewport_refresh:
            import_collection.hide_viewport = True
            
        blender_objs = {}
        local_matrices = {}
        mesh_cache = {}
        material_cache = {}
        parent_relations = {}
        decal_objs = []
        
        last_yield_time = time.time()
        time_slice = 0.25  # Yield every 250ms for responsive UI and progress updates
        
        for i, (package_path, idx, entity) in enumerate(all_entities):
            if time.time() - last_yield_time > time_slice:
                mytool.import_status = f"Importing components ({i}/{total_objects})..."
                mytool.import_progress = 10.0 + (i / total_objects) * 70.0
                yield False
                last_yield_time = time.time()
                
            etype = entity.get("Type", "")
            ename = entity.get("Name", f"Obj_{idx}")
            global_key = (package_path, idx)
            
            outer_dict = entity.get("Outer")
            is_actor = False
            outer_key = None
            if outer_dict:
                outer_name = outer_dict.get("ObjectName", "")
                if outer_name.startswith("Level'") or outer_name.startswith("World'"):
                    is_actor = True
                else:
                    outer_key = parse_reference_key(outer_dict, package_path)
            else:
                is_actor = True
                
            props = entity.get("Properties", {})
            
            template_ref = entity.get("Template")
            if template_ref and file_index:
                template_path = template_ref.get("ObjectPath")
                if template_path:
                    template_props = resolve_blueprint_properties(file_index, template_path)
                    props = {**template_props, **props}
            
            if is_actor:
                label = entity.get("ActorLabel", ename)
                actor_empty = bpy.data.objects.new(name=label, object_data=None)
                import_collection.objects.link(actor_empty)
                blender_objs[global_key] = actor_empty
                local_matrices[global_key] = mathutils.Matrix.Identity(4)
                continue
                
            loc = props.get("RelativeLocation", {})
            rot = props.get("RelativeRotation", {})
            scale = props.get("RelativeScale3D", {})
            local_matrices[global_key] = get_blender_transform(loc, rot, scale)
            
            attach_parent = props.get("AttachParent")
            attach_parent_key = parse_reference_key(attach_parent, package_path) if attach_parent else None
            
            if attach_parent_key:
                parent_relations[global_key] = attach_parent_key
            elif outer_key:
                parent_relations[global_key] = outer_key
                
            if skip_missing_materials and should_skip_component_due_to_missing_materials(etype, props, file_index):
                print(f"Skipping component {ename} because its overridden materials are missing from disk.")
                continue
                
            if etype in ("StaticMeshComponent", "InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent", "SkeletalMeshComponent"):
                sm_ref = props.get("StaticMesh") or props.get("SkeletalMesh")
                mesh_path = None
                if sm_ref:
                    mesh_path = sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
                else:
                    template_ref = entity.get("Template")
                    if template_ref:
                        template_path = template_ref.get("ObjectPath")
                        mesh_path = resolve_blueprint_mesh(file_index, template_path)
                        
                if not mesh_path:
                    if not skip_missing_assets:
                        empty_obj = bpy.data.objects.new(name=ename, object_data=None)
                        import_collection.objects.link(empty_obj)
                        blender_objs[global_key] = empty_obj
                    continue
                    
                instances = entity.get("PerInstanceSMData")
                if not instances and "Properties" in entity:
                    instances = entity.get("Properties", {}).get("PerInstanceSMData")
                    
                if not instances and file_index:
                    template_ref = entity.get("Template")
                    if template_ref:
                        template_path = template_ref.get("ObjectPath")
                        if template_path:
                            template_obj = resolve_blueprint_object(file_index, template_path)
                            if template_obj:
                                instances = template_obj.get("PerInstanceSMData")
                                if not instances and "Properties" in template_obj:
                                    instances = template_obj.get("Properties", {}).get("PerInstanceSMData")
                                    
                if instances and etype in ("HierarchicalInstancedStaticMeshComponent", "InstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent"):
                    print(f"Creating Instanced Component {ename} ({len(instances)} instances)")
                    comp_empty = bpy.data.objects.new(name=ename, object_data=None)
                    import_collection.objects.link(comp_empty)
                    blender_objs[global_key] = comp_empty
                    
                    base_mesh = None
                    if mesh_path in mesh_cache:
                        base_mesh = mesh_cache[mesh_path]
                    else:
                        if is_basic_shape(mesh_path):
                            base_mesh = create_basic_shape(mesh_path, f"{ename}_Template", import_collection)
                            mesh_cache[mesh_path] = base_mesh
                            if base_mesh:
                                base_mesh.hide_viewport = True
                                base_mesh.hide_render = True
                        else:
                            mesh_file = resolve_file_path(file_index, mesh_path, preferred_exts={'.gltf', '.glb', '.fbx', '.obj'})
                            if mesh_file:
                                base_mesh = import_asset(mesh_file, f"{ename}_Template", import_collection, hide_collisions)
                            mesh_cache[mesh_path] = base_mesh
                            if base_mesh:
                                base_mesh.hide_viewport = True
                                base_mesh.hide_render = True
                                
                    if not base_mesh:
                        print(f"Asset file not found or failed to import for: {mesh_path}")
                        continue
                        
                    if base_mesh:
                        num_instances = len(instances)
                        for inst_idx, inst in enumerate(instances):
                            if inst_idx % 100 == 0 and time.time() - last_yield_time > time_slice:
                                mytool.import_status = f"Importing components ({i}/{total_objects}) - Instancing {ename} ({inst_idx}/{num_instances})..."
                                mytool.import_progress = 10.0 + ((i + (inst_idx / num_instances)) / total_objects) * 70.0
                                yield False
                                last_yield_time = time.time()
                            trans_data = inst.get("TransformData", {})
                            if not trans_data:
                                continue
                            inst_loc = trans_data.get("Translation", {})
                            inst_rot = trans_data.get("Rotation", {})
                            inst_scale = trans_data.get("Scale3D", {})
                            inst_matrix = get_blender_transform(inst_loc, inst_rot, inst_scale)
                            
                            clone_obj = clone_hierarchy(base_mesh, f"{ename}_inst_{inst_idx}", import_collection, unhide=True, hide_collisions=hide_collisions)
                            clone_obj.parent = comp_empty
                            clone_obj.matrix_basis = inst_matrix
                            
                else:
                    mesh_obj = None
                    if mesh_path in mesh_cache:
                        cached_mesh = mesh_cache[mesh_path]
                        if cached_mesh:
                            mesh_obj = clone_hierarchy(cached_mesh, ename, import_collection, unhide=True, hide_collisions=hide_collisions)
                    else:
                        if is_basic_shape(mesh_path):
                            mesh_obj = create_basic_shape(mesh_path, ename, import_collection)
                            mesh_cache[mesh_path] = mesh_obj
                            if mesh_obj:
                                override_mats = props.get("OverrideMaterials", [])
                                if override_mats and mesh_obj.type == 'MESH':
                                    mesh_obj.data.materials.clear()
                                    for mat_ref in override_mats:
                                        if mat_ref:
                                            mat_path = mat_ref.get("ObjectPath") or mat_ref.get("ObjectName") or ""
                                            mat_clean = mat_path.split("'")[1] if "'" in mat_path else mat_path
                                            mat_clean = mat_clean.split(".")[0].split("/")[-1]
                                            mat = material_cache.get(mat_clean)
                                            if not mat:
                                                mat = bpy.data.materials.get(mat_clean)
                                                if not mat:
                                                    mat = bpy.data.materials.new(name=mat_clean)
                                                    mat.use_nodes = True
                                                material_cache[mat_clean] = mat
                                            mesh_obj.data.materials.append(mat)
                        else:
                            mesh_file = resolve_file_path(file_index, mesh_path, preferred_exts={'.gltf', '.glb', '.fbx', '.obj'})
                            if mesh_file:
                                mesh_obj = import_asset(mesh_file, ename, import_collection, hide_collisions)
                            mesh_cache[mesh_path] = mesh_obj
                                
                    if mesh_obj:
                        blender_objs[global_key] = mesh_obj
                    else:
                        if not skip_missing_assets:
                            empty_obj = bpy.data.objects.new(name=ename, object_data=None)
                            import_collection.objects.link(empty_obj)
                            blender_objs[global_key] = empty_obj
                        
            elif etype in ("SpotLightComponent", "PointLightComponent", "DirectionalLightComponent", "RectLightComponent"):
                light_type = 'POINT'
                if etype == "SpotLightComponent":
                    light_type = 'SPOT'
                elif etype == "DirectionalLightComponent":
                    light_type = 'SUN'
                elif etype == "RectLightComponent":
                    light_type = 'AREA'
                    
                light_data = bpy.data.lights.new(name=ename, type=light_type)
                intensity = props.get("Intensity", 0.0)
                if intensity > 0.0:
                    light_data.energy = intensity / 100.0
                    
                color_dict = props.get("LightColor", {})
                if color_dict:
                    light_data.color = (color_dict.get("R", 255)/255.0, color_dict.get("G", 255)/255.0, color_dict.get("B", 255)/255.0)
                    
                light_obj = bpy.data.objects.new(name=ename, object_data=light_data)
                import_collection.objects.link(light_obj)
                blender_objs[global_key] = light_obj
                
            elif etype == "DecalComponent":
                if not import_decals:
                    continue
                mat_ref = props.get("DecalMaterial")
                mat_name = None
                if mat_ref:
                    obj_name = mat_ref.get("ObjectName", "")
                    mat_name = obj_name.split("'")[1] if "'" in obj_name else obj_name
                
                material_obj = None
                if mat_name:
                    material_obj = material_cache.get(mat_name)
                    if not material_obj:
                        material_obj = bpy.data.materials.get(mat_name)
                        if not material_obj:
                            material_obj = bpy.data.materials.new(name=mat_name)
                            material_obj.use_nodes = True
                        material_cache[mat_name] = material_obj
                        
                decal_obj = create_decal_plane_mesh(ename, material_obj, import_collection)
                blender_objs[global_key] = decal_obj
                decal_objs.append((decal_obj, props))
                
                decal_size = props.get("DecalSize", {})
                if decal_size:
                    ds_x = decal_size.get("X", 128.0) / 128.0
                    ds_y = decal_size.get("Y", 128.0) / 128.0
                    ds_z = decal_size.get("Z", 128.0) / 128.0
                    scale_decal_mat = mathutils.Matrix.Diagonal((ds_x, ds_y, ds_z, 1.0))
                    local_matrices[global_key] = local_matrices[global_key] @ scale_decal_mat
                
            else:
                empty_obj = bpy.data.objects.new(name=ename, object_data=None)
                import_collection.objects.link(empty_obj)
                blender_objs[global_key] = empty_obj
                
        mytool.import_status = "Establishing parenting structures..."
        mytool.import_progress = 85.0
        yield False
        
        for key, obj in blender_objs.items():
            if key in parent_relations:
                p_key = parent_relations[key]
                if p_key in blender_objs:
                    obj.parent = blender_objs[p_key]
                    
        if skip_missing_assets:
            mytool.import_status = "Cleaning up empty placeholders..."
            mytool.import_progress = 90.0
            yield False
            
            def get_depth(obj):
                depth = 0
                while obj.parent:
                    depth += 1
                    obj = obj.parent
                return depth
                
            empties = [(key, obj) for key, obj in blender_objs.items() if obj.type == 'EMPTY']
            empties.sort(key=lambda x: get_depth(x[1]), reverse=True)
            
            for key, obj in empties:
                if not obj.children:
                    if obj.name in import_collection.objects:
                        import_collection.objects.unlink(obj)
                    bpy.data.objects.remove(obj, do_unlink=True)
                    del blender_objs[key]
                    if key in local_matrices:
                        del local_matrices[key]
                    
        mytool.import_status = "Assigning transformations..."
        mytool.import_progress = 95.0
        yield False
        
        for key, obj in blender_objs.items():
            if key in local_matrices:
                obj.matrix_basis = local_matrices[key]
                
        # Decal Projection/Snapping Phase
        if decal_objs:
            mytool.import_status = "Projecting decals onto walls..."
            mytool.import_progress = 97.0
            yield False
            
            # Temporarily hide all decals to prevent raycast self-intersection
            for decal_obj, _ in decal_objs:
                decal_obj.hide_viewport = True
            
            # Temporarily show the collection so Blender can build the dependency graph for raycasting
            if import_collection:
                import_collection.hide_viewport = False
            
            context.view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()
            
            for decal_obj, props in decal_objs:
                try:
                    # Get decal size
                    decal_size = props.get("DecalSize", {})
                    # Default X size in Unreal is 128.0 cm = 1.28 meters
                    depth = decal_size.get("X", 128.0) / 100.0
                    
                    # Raycast origin and direction in world space
                    world_matrix = decal_obj.matrix_world
                    # Start at the back of the projection box (local X = -depth/2)
                    ray_origin = world_matrix @ mathutils.Vector((-depth / 2.0, 0.0, 0.0))
                    # Raycast along the local +X axis
                    ray_direction = (world_matrix.to_3x3() @ mathutils.Vector((1.0, 0.0, 0.0))).normalized()
                    
                    success, hit_loc, hit_norm, hit_index, hit_obj, hit_matrix = context.scene.ray_cast(
                        depsgraph, ray_origin, ray_direction, distance=depth
                    )
                    if success and hit_obj and hit_obj.type == 'MESH':
                        # Ignore other decals or physical collisions
                        if "decal" not in hit_obj.name.lower() and not is_collision_mesh(hit_obj.name):
                            # Add a Shrinkwrap modifier to snap the decal mesh to the wall
                            mod = decal_obj.modifiers.new(name="DecalProject", type='SHRINKWRAP')
                            mod.target = hit_obj
                            mod.wrap_method = 'PROJECT'
                            mod.wrap_mode = 'ON_SURFACE'
                            mod.project_limit = depth
                            mod.use_project_x = True
                            mod.use_project_y = False
                            mod.use_project_z = False
                            mod.use_negative_direction = False
                            mod.use_positive_direction = True
                            mod.offset = 0.0015  # 1.5mm offset to prevent Z-fighting
                except Exception as e:
                    print(f"Error projecting decal {decal_obj.name}: {e}")
            
            # Restore visibility for all decals
            for decal_obj, _ in decal_objs:
                decal_obj.hide_viewport = False
            
            # Hide the collection again if disable_viewport_refresh was active
            if mytool.disable_viewport_refresh and import_collection:
                import_collection.hide_viewport = True
                
        # Trigger materials rebuild
        mytool.import_status = "Importing materials..."
        mytool.import_progress = 98.0
        yield False
        
        bpy.ops.lis.mat_import()
        
        mytool.import_status = "Import complete!"
        mytool.import_progress = 100.0
        if mytool.disable_viewport_refresh and import_collection:
            import_collection.hide_viewport = False
        yield True

def find_fallback_textures(file_index, mat_name, mat_file_path, base_dir):
    """
    Attempts to find matching textures (Diffuse, Normal, ORM/AORM, Emissive) on disk
    by performing a smart keyword and folder-proximity search in the file index.
    Used as a fallback when a material instance JSON is empty or missing texture references.
    """
    if not file_index or not base_dir:
        return {}
        
    import re
    import os
    
    # 1. Tokenize and clean material name to extract search keywords
    name_lower = mat_name.lower()
    
    # Strip common prefixes
    for prefix in ("mi_", "m_", "mm_", "mli_", "mlb_", "t_", "tx_"):
        if name_lower.startswith(prefix):
            name_lower = name_lower[len(prefix):]
            break
            
    # Strip common suffixes/variants
    name_clean = re.sub(r'(_inst|_constant|_mat|_material|_\d+|_[a-z])+$', '', name_lower)
    
    # Split by separators to get keyword tokens
    keywords = [k for k in re.split(r'[-_\s]', name_clean) if k and len(k) >= 3]
    
    # Fallback to name_clean if all tokens are too short
    if not keywords and name_clean:
        keywords = [name_clean]
        
    if not keywords:
        return {}
        
    # We use the last keyword as the primary search term (e.g. "concrete" in "basic_concrete")
    search_keyword = keywords[-1]
    
    # 2. Search index for candidate image files containing the search keyword
    img_exts = {'.png', '.tga', '.jpg', '.jpeg', '.dds'}
    candidates = []
    
    for key, paths in file_index.items():
        if search_keyword in key:
            for path in paths:
                ext = os.path.splitext(path)[1].lower()
                if ext in img_exts:
                    candidates.append(path)
                    
    if not candidates:
        return {}
        
    # Deduplicate candidates
    candidates = list(set(candidates))
    
    # 3. Categorize candidates by texture type based on filename suffix pattern
    diffuse_cands = []
    normal_cands = []
    mask_cands = []
    emissive_cands = []
    
    for path in candidates:
        fname = os.path.splitext(os.path.basename(path))[0].lower()
        
        # Check normal map patterns
        if any(x in fname for x in ("_n", "normal", "nrm", "normals", "nmap")):
            normal_cands.append(path)
        # Check diffuse/color patterns
        elif any(x in fname for x in ("_c", "_d", "basecolor", "albedo", "color", "diffuse", "_bc", "colour")):
            diffuse_cands.append(path)
        # Check mask/AORM/ORM patterns
        elif any(x in fname for x in ("_orm", "_aorm", "_mask", "mask", "masks", "_mra", "_srm", "metallicroughness")):
            mask_cands.append(path)
        # Check emissive patterns
        elif any(x in fname for x in ("_e", "emissive", "emiss", "glow")):
            emissive_cands.append(path)
            
    # 4. Scoring function to find the best candidate in each category
    def score_candidate(path):
        fname = os.path.splitext(os.path.basename(path))[0].lower()
        tex_dir = os.path.dirname(path).replace('\\', '/').lower()
        mat_dir = os.path.dirname(mat_file_path).replace('\\', '/').lower() if mat_file_path else None
        
        score = 0
        
        # Match keywords in filename
        matched_kws = 0
        for kw in keywords:
            if kw in fname:
                score += 15
                matched_kws += 1
                
        # Bonus if all keywords matched
        if matched_kws == len(keywords):
            score += 35
            
        # Perfect match bonus: strip prefix/suffix and compare
        stripped_tex = re.sub(r'^(t_|tx_)', '', fname)
        stripped_tex = re.sub(r'(_[cnd]|_basecolor|_normal|_orm|_aorm|_albedo|_color|_diffuse|_bc|_e|_emissive)+$', '', stripped_tex)
        if stripped_tex == name_clean:
            score += 50
            
        # Penalty if texture is specialized but material name is not
        specialized_words = {
            "plaster", "patch", "rubble", "block", "wall", "ceiling", "floor", "fence",
            "column", "beam", "brick", "pipe", "radiator", "railing", "shelf", "sign",
            "window", "barrier", "post", "panel", "tile", "stair", "door", "steps"
        }
        for word in specialized_words:
            if word in fname and word not in name_lower:
                score -= 40
                
        # Global shared directory bonus
        if "materiallayer" in tex_dir:
            score += 5
            
        # Folder proximity: count shared directory components relative to base_dir
        if mat_dir:
            try:
                mat_rel = os.path.relpath(mat_dir, base_dir).replace('\\', '/').split('/')
                tex_rel = os.path.relpath(tex_dir, base_dir).replace('\\', '/').split('/')
                common = 0
                for i in range(min(len(mat_rel), len(tex_rel))):
                    if mat_rel[i] == tex_rel[i]:
                        common += 1
                    else:
                        break
                score += common * 10
            except Exception:
                pass
            
        # Length penalty to favor cleaner, shorter names
        score -= len(fname) * 0.2
        
        return score
        
    fallback = {}
    
    if diffuse_cands:
        diffuse_cands.sort(key=score_candidate, reverse=True)
        fallback["diffuse"] = os.path.splitext(os.path.basename(diffuse_cands[0]))[0]
        
    if normal_cands:
        normal_cands.sort(key=score_candidate, reverse=True)
        fallback["normal"] = os.path.splitext(os.path.basename(normal_cands[0]))[0]
        
    if mask_cands:
        mask_cands.sort(key=score_candidate, reverse=True)
        fallback["mask"] = os.path.splitext(os.path.basename(mask_cands[0]))[0]
        
    if emissive_cands:
        emissive_cands.sort(key=score_candidate, reverse=True)
        fallback["emissive"] = os.path.splitext(os.path.basename(emissive_cands[0]))[0]
        
    return fallback

class MaterialImporter(bpy.types.Operator):
    bl_idname = "lis.mat_import"
    bl_label = "Material Importer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        mytool = scene.my_tool
        base_dir = mytool.base_directory
        
        # Index asset directory for materials/textures
        file_index = get_cached_index(base_dir)
        
        # Material deduplication
        materials = bpy.data.materials
        for material in list(materials):
            mat_name = material.name
            if '.' in mat_name:
                parts = mat_name.split('.')
                if parts[-1].isdigit():
                    base_mat_name = ".".join(parts[:-1])
                    base_mat = materials.get(base_mat_name)
                    if base_mat:
                        material.user_remap(base_mat)
                        materials.remove(material, do_unlink=True)
                    else:
                        material.name = base_mat_name
                    
        material_data_cache = {}

        def load_material_data(file_path, depth=0):
            if not file_path or not os.path.exists(file_path):
                return {}, {}, {}, None

            if file_path in material_data_cache:
                tex, scal, vec, mdata = material_data_cache[file_path]
                return dict(tex), dict(scal), dict(vec), mdata

            textures = {}
            scalars = {}
            vectors = {}
            main_mat_data = []

            def add_tex(k, v):
                if not k or not v:
                    return
                k_lower = k.lower()
                textures[k_lower] = v
                k_norm = k_lower.replace(" ", "").replace("_", "").replace("-", "")
                if k_norm not in textures:
                    textures[k_norm] = v

            if file_path.endswith('.json'):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        mat_data = json.load(f)
                    
                    if isinstance(mat_data, list):
                        main_mat_data.extend(mat_data)
                    elif isinstance(mat_data, dict):
                        main_mat_data.append(mat_data)

                    def extract_local_params(data_item):
                        if isinstance(data_item, dict):
                            if "Textures" in data_item and isinstance(data_item["Textures"], dict):
                                for k, v in data_item["Textures"].items():
                                    if isinstance(v, str):
                                        tex_name = v.split("'")[1] if "'" in v else v
                                        add_tex(k, tex_name.split('.')[0])
                                        
                            tp_vals = data_item.get("TextureParameterValues", [])
                            if isinstance(tp_vals, list):
                                for tp in tp_vals:
                                    if isinstance(tp, dict):
                                        param_info = tp.get("ParameterInfo", {})
                                        param_name = param_info.get("Name", "")
                                        param_index = param_info.get("Index", -1)
                                        param_val = tp.get("ParameterValue")
                                        if param_val:
                                            if isinstance(param_val, dict):
                                                obj_name = param_val.get("ObjectName") or param_val.get("ObjectPath") or ""
                                            else:
                                                obj_name = str(param_val)
                                            if obj_name:
                                                tex_name = obj_name.split("'")[1] if "'" in obj_name else obj_name
                                                clean_tex = tex_name.split('.')[0]
                                                add_tex(param_name, clean_tex)
                                                if param_index != -1:
                                                    add_tex(f"{param_name}_{param_index}", clean_tex)
                                                    add_tex(f"{param_name}{param_index}", clean_tex)
                                                
                            sp_vals = data_item.get("ScalarParameterValues", [])
                            if isinstance(sp_vals, list):
                                for sp in sp_vals:
                                    if isinstance(sp, dict):
                                        param_info = sp.get("ParameterInfo", {})
                                        param_name = param_info.get("Name", "")
                                        param_index = param_info.get("Index", -1)
                                        param_val = sp.get("ParameterValue")
                                        if param_name and param_val is not None:
                                            try:
                                                val_float = float(param_val)
                                                scalars[param_name.lower()] = val_float
                                                if param_index != -1:
                                                    scalars[f"{param_name.lower()}_{param_index}"] = val_float
                                                    scalars[f"{param_name.lower()}{param_index}"] = val_float
                                            except (ValueError, TypeError):
                                                pass
                                                
                            vp_vals = data_item.get("VectorParameterValues", [])
                            if isinstance(vp_vals, list):
                                for vp in vp_vals:
                                    if isinstance(vp, dict):
                                        param_info = vp.get("ParameterInfo", {})
                                        param_name = param_info.get("Name", "")
                                        param_index = param_info.get("Index", -1)
                                        param_val = vp.get("ParameterValue")
                                        if param_name and isinstance(param_val, dict):
                                            val_vec = (
                                                param_val.get("R", 0.0),
                                                param_val.get("G", 0.0),
                                                param_val.get("B", 0.0),
                                                param_val.get("A", 1.0)
                                            )
                                            vectors[param_name.lower()] = val_vec
                                            if param_index != -1:
                                                vectors[f"{param_name.lower()}_{param_index}"] = val_vec
                                                vectors[f"{param_name.lower()}{param_index}"] = val_vec
                                            
                            for k, v in data_item.items():
                                k_lower = k.lower()
                                if k_lower in ("textureparametervalues", "textures", "scalarparametervalues", "vectorparametervalues", "cachedexpressiondata"):
                                    continue
                                if isinstance(v, (int, float)):
                                    scalars[k_lower] = float(v)
                                elif isinstance(v, dict):
                                    if any(x in v for x in ("R", "G", "B")):
                                        vectors[k_lower] = (
                                            v.get("R", 0.0),
                                            v.get("G", 0.0),
                                            v.get("B", 0.0),
                                            v.get("A", 1.0)
                                        )
                                    else:
                                        extract_local_params(v)
                                elif isinstance(v, str):
                                    if "/textures/" in v.lower() or "texture2d'" in v.lower():
                                        tex_name = v.split("'")[1] if "'" in v else v
                                        add_tex(k, tex_name.split('.')[0])
                                elif isinstance(v, list):
                                    extract_local_params(v)
 
                            cached = data_item.get("CachedExpressionData")
                            if isinstance(cached, dict):
                                runtime_scalars = cached.get("RuntimeEntries") or cached.get("RuntimeEntries[0]")
                                if isinstance(runtime_scalars, dict):
                                    p_info = runtime_scalars.get("ParameterInfoSet", [])
                                    scalar_vals = cached.get("ScalarValues", [])
                                    for idx, p in enumerate(p_info):
                                        if idx < len(scalar_vals):
                                            name = p.get("Name")
                                            p_index = p.get("Index", -1)
                                            if name:
                                                val_float = float(scalar_vals[idx])
                                                scalars[name.lower()] = val_float
                                                if p_index != -1:
                                                    scalars[f"{name.lower()}_{p_index}"] = val_float
                                                    scalars[f"{name.lower()}{p_index}"] = val_float
                                
                                runtime_vectors = cached.get("RuntimeEntries[1]")
                                if isinstance(runtime_vectors, dict):
                                    p_info = runtime_vectors.get("ParameterInfoSet", [])
                                    vector_vals = cached.get("VectorValues", [])
                                    for idx, p in enumerate(p_info):
                                        if idx < len(vector_vals):
                                            name = p.get("Name")
                                            p_index = p.get("Index", -1)
                                            val = vector_vals[idx]
                                            if name and isinstance(val, dict):
                                                val_vec = (
                                                    val.get("R", 0.0),
                                                    val.get("G", 0.0),
                                                    val.get("B", 0.0),
                                                    val.get("A", 1.0)
                                                )
                                                vectors[name.lower()] = val_vec
                                                if p_index != -1:
                                                    vectors[f"{name.lower()}_{p_index}"] = val_vec
                                                    vectors[f"{name.lower()}{p_index}"] = val_vec
                                                
                                runtime_textures = cached.get("RuntimeEntries[3]")
                                if isinstance(runtime_textures, dict):
                                    p_info = runtime_textures.get("ParameterInfoSet", [])
                                    tex_vals = cached.get("TextureValues", [])
                                    for idx, p in enumerate(p_info):
                                        if idx < len(tex_vals):
                                            name = p.get("Name")
                                            p_index = p.get("Index", -1)
                                            val = tex_vals[idx]
                                            if name and isinstance(val, dict):
                                                asset_path = val.get("AssetPathName")
                                                if asset_path:
                                                    tex_name = asset_path.split("'")[1] if "'" in asset_path else asset_path
                                                    clean_tex = tex_name.split('.')[0]
                                                    add_tex(name, clean_tex)
                                                    if p_index != -1:
                                                        add_tex(f"{name}_{p_index}", clean_tex)
                                                        add_tex(f"{name}{p_index}", clean_tex)
                        elif isinstance(data_item, list):
                            for item in data_item:
                                extract_local_params(item)
                                
                    extract_local_params(mat_data)
                    
                    parent_ref = None
                    if isinstance(mat_data, list):
                        for item in mat_data:
                            if isinstance(item, dict):
                                props = item.get("Properties", {})
                                if "Parent" in props:
                                    parent_ref = props["Parent"]
                                    break
                    elif isinstance(mat_data, dict):
                        props = mat_data.get("Properties", {})
                        if "Parent" in props:
                            parent_ref = props["Parent"]
                            
                    if parent_ref and depth < 5:
                        parent_path_raw = parent_ref.get("ObjectPath") or parent_ref.get("ObjectName")
                        clean_parent_path = clean_unreal_path(parent_path_raw)
                        if clean_parent_path:
                            parent_file = resolve_file_path(file_index, clean_parent_path, preferred_exts={'.json', '.mat'})
                            if parent_file:
                                parent_tex, parent_scalars, parent_vectors, parent_mdata = load_material_data(parent_file, depth + 1)
                                parent_tex.update(textures)
                                textures = parent_tex
                                parent_scalars.update(scalars)
                                scalars = parent_scalars
                                parent_vectors.update(vectors)
                                vectors = parent_vectors
                                if parent_mdata:
                                    main_mat_data.extend(parent_mdata)
                                
                except Exception as e:
                    print(f"Error parsing material JSON {file_path}: {e}")
            else:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    for line in lines:
                        if '=' in line:
                            k, v = line.split('=', 1)
                            k_clean = k.strip().lower()
                            v_clean = v.strip()
                            try:
                                scalars[k_clean] = float(v_clean)
                            except ValueError:
                                add_tex(k_clean, v_clean)
                except Exception as e:
                    print(f"Error parsing legacy mat file {file_path}: {e}")

            material_data_cache[file_path] = (dict(textures), dict(scalars), dict(vectors), main_mat_data)
            return textures, scalars, vectors, main_mat_data

        # Material rebuilds
        print("Configuring material shaders...")
        for material in list(materials):
            mat_name = material.name
            
            if 'WorldGridMaterial' in mat_name:
                continue
                
            mat_file = resolve_file_path(file_index, mat_name, preferred_exts={'.json', '.mat'})
            if not mat_file:
                if mat_name.lower() == "basicshapematerial":
                    material.use_nodes = True
                    nodes = material.node_tree.nodes
                    links = material.node_tree.links
                    for node in list(nodes):
                        if node.type != 'OUTPUT_MATERIAL':
                            nodes.remove(node)
                            
                    material_output = nodes.get("Material Output")
                    if not material_output:
                        material_output = nodes.new(type="ShaderNodeOutputMaterial")
                        
                    shader_node = nodes.new(type="ShaderNodeBsdfPrincipled")
                    links.new(material_output.inputs["Surface"], shader_node.outputs["BSDF"])
                    
                    checker = nodes.new(type="ShaderNodeTexChecker")
                    checker.inputs["Color1"].default_value = (0.4, 0.4, 0.4, 1.0)
                    checker.inputs["Color2"].default_value = (0.5, 0.5, 0.5, 1.0)
                    checker.inputs["Scale"].default_value = 10.0
                    
                    links.new(shader_node.inputs["Base Color"], checker.outputs["Color"])
                    shader_node.inputs["Roughness"].default_value = 0.5
                    print("Generated default procedural grid for BasicShapeMaterial")
                    continue
                else:
                    print(f"Material file not found for: {mat_name}, attempting fuzzy fallback...")
                    textures = {}
                    scalars = {}
                    vectors = {}
                    mat_data = []
            else:
                textures, scalars, vectors, mat_data = load_material_data(mat_file)
            
            # Fuzzy match fallback when material has empty/null textures
            if not textures and file_index:
                fallback_tex = find_fallback_textures(file_index, mat_name, mat_file, base_dir)
                if fallback_tex:
                    print(f"Using fuzzy fallback textures for {mat_name}: {fallback_tex}")
                    textures.update(fallback_tex)
                    
            if not textures and not scalars and not vectors:
                continue
                
            # Check if Blender already has textures for this material
            has_existing_textures = False
            if material.use_nodes and material.node_tree:
                has_existing_textures = any(node.type == 'TEX_IMAGE' for node in material.node_tree.nodes)
                
            # If the JSON has no textures, but Blender already has textures, preserve them!
            if not textures and has_existing_textures:
                print(f"Preserving existing textures for material: {mat_name}")
                continue
                
            # Extract Set A texture parameter mappings (Layer 1/Base)
            diffuse_tex = (
                textures.get("diffuse") or 
                textures.get("basecolor") or 
                textures.get("basecolortexture") or 
                textures.get("base_color_texture") or 
                textures.get("basecolorcomponent") or 
                textures.get("basecolortex") or 
                textures.get("color") or 
                textures.get("albedo") or 
                textures.get("diffuse1") or 
                textures.get("diffusetexture") or 
                textures.get("other[0]") or 
                textures.get("pm_diffuse") or
                textures.get("l1albedo") or 
                textures.get("l1diffuse") or 
                textures.get("l1basecolor") or 
                textures.get("l1color") or 
                textures.get("albedo_0") or 
                textures.get("albedo0") or 
                textures.get("albedo_1") or 
                textures.get("albedo1") or 
                textures.get("diffuse_0") or 
                textures.get("diffuse0") or 
                textures.get("diffuse_1") or 
                textures.get("diffuse1") or 
                textures.get("basecolor_0") or 
                textures.get("basecolor0") or 
                textures.get("basecolor_1") or 
                textures.get("basecolor1")
            )
            normal_tex = (
                textures.get("normal") or 
                textures.get("normaltex") or 
                textures.get("normaltexture") or 
                textures.get("normalmap") or 
                textures.get("normalcomponent") or 
                textures.get("pm_normals") or
                textures.get("l1normal") or 
                textures.get("l1normalmap") or 
                textures.get("l1norm") or 
                textures.get("normal_0") or 
                textures.get("normal0") or 
                textures.get("normal_1") or 
                textures.get("normal1")
            )
            spec_tex = (
                textures.get("specular") or 
                textures.get("spec") or 
                textures.get("specpower") or
                textures.get("l1specular") or 
                textures.get("l1spec") or 
                textures.get("specular_0") or 
                textures.get("specular0") or 
                textures.get("specular_1") or 
                textures.get("specular1")
            )
            rough_tex = (
                textures.get("roughness") or 
                textures.get("rough") or 
                textures.get("roughnesstex") or
                textures.get("l1roughness") or 
                textures.get("l1rough") or 
                textures.get("roughness_0") or 
                textures.get("roughness0") or 
                textures.get("roughness_1") or 
                textures.get("roughness1")
            )
            metallic_tex = (
                textures.get("metallic") or 
                textures.get("metal") or 
                textures.get("metallictex") or
                textures.get("l1metallic") or 
                textures.get("l1metal") or 
                textures.get("metallic_0") or 
                textures.get("metallic0") or 
                textures.get("metallic_1") or 
                textures.get("metallic1")
            )
            mask_tex = (
                textures.get("masks") or 
                textures.get("mask") or 
                textures.get("maskmap") or 
                textures.get("maskstex") or 
                textures.get("mra") or 
                textures.get("ormh") or 
                textures.get("orm") or 
                textures.get("orme") or 
                textures.get("orme(aoroughmatemis)") or 
                textures.get("srm") or 
                textures.get("pm_specularmasks") or 
                textures.get("specularmasks") or
                textures.get("l1mask") or 
                textures.get("l1orm") or 
                textures.get("l1aorm") or 
                textures.get("l1srm") or 
                textures.get("l1mra") or 
                textures.get("mask_0") or 
                textures.get("mask0") or 
                textures.get("mask_1") or 
                textures.get("mask1") or 
                textures.get("aorm_0") or 
                textures.get("aorm0") or 
                textures.get("aorm_1") or 
                textures.get("aorm1") or 
                textures.get("orm_0") or 
                textures.get("orm0") or 
                textures.get("orm_1") or 
                textures.get("orm1")
            )
            emissive_tex = textures.get("emissive") or textures.get("emissivecolor") or textures.get("emissivetex") or textures.get("emissive_color") or textures.get("emissive_tex") or textures.get("pm_emissive")
            
            # Extract Set B texture parameter mappings (Layer 2/Blend)
            diffuse_tex_b = (
                textures.get("diffuse_b") or 
                textures.get("diffuse2") or 
                textures.get("basecolor_b") or 
                textures.get("basecolor_2") or 
                textures.get("basecolortexture_b") or 
                textures.get("basecolor_texture_b") or 
                textures.get("basecolorcomponent_b") or 
                textures.get("basecolortex_b") or 
                textures.get("color_b") or 
                textures.get("albedo_b") or 
                textures.get("wornbasecolortexture") or 
                textures.get("worndiffusetexture") or 
                textures.get("wornalbedo") or 
                textures.get("albedodamaget") or 
                textures.get("diffusedamaget") or 
                textures.get("basecolordamage") or 
                textures.get("other[1]") or
                textures.get("l2albedo") or 
                textures.get("l2diffuse") or 
                textures.get("l2basecolor") or 
                textures.get("l2color") or 
                textures.get("albedo_2") or 
                textures.get("albedo2") or 
                textures.get("albedo_3") or 
                textures.get("albedo3") or 
                textures.get("diffuse_2") or 
                textures.get("diffuse2") or 
                textures.get("diffuse_3") or 
                textures.get("diffuse3") or 
                textures.get("basecolor_2") or 
                textures.get("basecolor2") or 
                textures.get("basecolor_3") or 
                textures.get("basecolor3")
            )
            normal_tex_b = (
                textures.get("normal_b") or 
                textures.get("normal_2") or 
                textures.get("normaltex_b") or 
                textures.get("normaltexture_b") or 
                textures.get("normalmap_b") or 
                textures.get("normalcomponent_b") or 
                textures.get("wornnormal") or 
                textures.get("wornnormaltexture") or 
                textures.get("wornnormalmap") or 
                textures.get("damagenormalt") or 
                textures.get("damagenormal") or
                textures.get("l2normal") or 
                textures.get("l2normalmap") or 
                textures.get("l2norm") or 
                textures.get("normal_2") or 
                textures.get("normal2") or 
                textures.get("normal_3") or 
                textures.get("normal3")
            )
            rough_tex_b = (
                textures.get("roughness_b") or 
                textures.get("rough_b") or 
                textures.get("roughnesstex_b") or
                textures.get("l2roughness") or 
                textures.get("l2rough") or 
                textures.get("roughness_2") or 
                textures.get("roughness2") or 
                textures.get("roughness_3") or 
                textures.get("roughness3")
            )
            metallic_tex_b = (
                textures.get("metallic_b") or 
                textures.get("metal_b") or 
                textures.get("metallictex_b") or
                textures.get("l2metallic") or 
                textures.get("l2metal") or 
                textures.get("metallic_2") or 
                textures.get("metallic2") or 
                textures.get("metallic_3") or 
                textures.get("metallic3")
            )
            mask_tex_b = (
                textures.get("masks_b") or 
                textures.get("mask_b") or 
                textures.get("maskmap_b") or 
                textures.get("maskstex_b") or 
                textures.get("mra_b") or 
                textures.get("ormh_b") or 
                textures.get("orm_b") or 
                textures.get("orme_b") or 
                textures.get("orme(aoroughmatemis)_b") or 
                textures.get("srm_b") or 
                textures.get("pm_specularmasks_b") or 
                textures.get("specularmasks_b") or 
                textures.get("wornmask") or 
                textures.get("wornmaskmap") or 
                textures.get("wornmasks") or 
                textures.get("wornorm") or 
                textures.get("wornormh") or 
                textures.get("wornorme") or 
                textures.get("wornsrm") or
                textures.get("l2mask") or 
                textures.get("l2orm") or 
                textures.get("l2aorm") or 
                textures.get("l2srm") or 
                textures.get("l2mra") or 
                textures.get("mask_2") or 
                textures.get("mask2") or 
                textures.get("mask_3") or 
                textures.get("mask3") or 
                textures.get("aorm_2") or 
                textures.get("aorm2") or 
                textures.get("aorm_3") or 
                textures.get("aorm3") or 
                textures.get("orm_2") or 
                textures.get("orm2") or 
                textures.get("orm_3") or 
                textures.get("orm3")
            )
            
            # Resolve image file paths
            img_exts = {'.tga', '.png', '.jpg', '.jpeg', '.dds'}
            
            diffuse_path = resolve_file_path(file_index, diffuse_tex, preferred_exts=img_exts) if diffuse_tex else None
            normal_path = resolve_file_path(file_index, normal_tex, preferred_exts=img_exts) if normal_tex else None
            spec_path = resolve_file_path(file_index, spec_tex, preferred_exts=img_exts) if spec_tex else None
            rough_path = resolve_file_path(file_index, rough_tex, preferred_exts=img_exts) if rough_tex else None
            metallic_path = resolve_file_path(file_index, metallic_tex, preferred_exts=img_exts) if metallic_tex else None
            mask_path = resolve_file_path(file_index, mask_tex, preferred_exts=img_exts) if mask_tex else None
            emissive_path = resolve_file_path(file_index, emissive_tex, preferred_exts=img_exts) if emissive_tex else None
            
            diffuse_path_b = resolve_file_path(file_index, diffuse_tex_b, preferred_exts=img_exts) if diffuse_tex_b else None
            normal_path_b = resolve_file_path(file_index, normal_tex_b, preferred_exts=img_exts) if normal_tex_b else None
            rough_path_b = resolve_file_path(file_index, rough_tex_b, preferred_exts=img_exts) if rough_tex_b else None
            metallic_path_b = resolve_file_path(file_index, metallic_tex_b, preferred_exts=img_exts) if metallic_tex_b else None
            mask_path_b = resolve_file_path(file_index, mask_tex_b, preferred_exts=img_exts) if mask_tex_b else None
            
            # Clean node tree
            material.use_nodes = True
            for node in list(material.node_tree.nodes):
                if node.type != 'OUTPUT_MATERIAL':
                    material.node_tree.nodes.remove(node)
                    
            material_output = material.node_tree.nodes.get("Material Output")
            if not material_output:
                material_output = material.node_tree.nodes.new(type="ShaderNodeOutputMaterial")
                
            shader_node = material.node_tree.nodes.new(type="ShaderNodeBsdfPrincipled")
            material.node_tree.links.new(material_output.inputs["Surface"], shader_node.outputs["BSDF"])
            material.use_backface_culling = False
            
            # Detect secondary blending sets
            has_blend = diffuse_path_b or normal_path_b or rough_path_b or metallic_path_b or mask_path_b
            vcol_node = None
            if has_blend:
                # Add vertex color node (try ColorAttribute first, fallback to VertexColor for Blender 3.x)
                try:
                    vcol_node = material.node_tree.nodes.new(type="ShaderNodeColorAttribute")
                except RuntimeError:
                    vcol_node = material.node_tree.nodes.new(type="ShaderNodeVertexColor")
            
            def load_texture_node(path, colorspace):
                if not path:
                    return None
                try:
                    img_name = os.path.basename(path)
                    img = bpy.data.images.get(img_name)
                    if not img:
                        img = bpy.data.images.load(path)
                    try:
                        img.colorspace_settings.name = colorspace
                    except Exception:
                        pass
                    tex_node = material.node_tree.nodes.new(type="ShaderNodeTexImage")
                    tex_node.image = img
                    return tex_node
                except Exception as e:
                    print(f"Error loading texture {path}: {e}")
                return None
                
            def link_socket(tex_node, socket_name, out_socket_name="Color"):
                if not tex_node:
                    return
                socket = shader_node.inputs.get(socket_name)
                if socket is None and socket_name == "Specular":
                    socket = shader_node.inputs.get("Specular IOR Level")
                if socket is None and socket_name == "Emission Color":
                    socket = shader_node.inputs.get("Emission")
                if socket is not None:
                    material.node_tree.links.new(socket, tex_node.outputs[out_socket_name])
                    
            def create_mix_node(mat):
                """
                Generates a color Mix node. Version-independent wrapper for Blender 3.x vs 4.x.
                """
                try:
                    m_node = mat.node_tree.nodes.new(type="ShaderNodeMix")
                    m_node.data_type = 'RGBA'
                    m_node.blend_type = 'MIX'
                    return m_node, "Factor", "A", "B", "Result"
                except (RuntimeError, ValueError):
                    m_node = mat.node_tree.nodes.new(type="ShaderNodeMixRGB")
                    m_node.blend_type = 'MIX'
                    return m_node, "Fac", "Color1", "Color2", "Color"
                    
            # Detect if diffuse texture was requested but missing on disk
            diffuse_missing = bool(diffuse_tex and not diffuse_path)

            # 1. Base Color & Alpha Setup
            diffuse_node_a = load_texture_node(diffuse_path, "sRGB")
            diffuse_node_b = load_texture_node(diffuse_path_b, "sRGB")
            
            if diffuse_node_a:
                if diffuse_node_b and vcol_node:
                    # Blended color setup
                    mix_node, fac_in, a_in, b_in, out_res = create_mix_node(material)
                    material.node_tree.links.new(mix_node.inputs[fac_in], vcol_node.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[a_in], diffuse_node_a.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[b_in], diffuse_node_b.outputs["Color"])
                    material.node_tree.links.new(shader_node.inputs["Base Color"], mix_node.outputs[out_res])
                    
                    if is_material_transparent(mat_name, mat_data):
                        link_socket(diffuse_node_a, "Alpha", "Alpha")
                        try:
                            material.blend_method = 'HASHED'
                        except (AttributeError, TypeError):
                            pass
                        try:
                            material.shadow_method = 'HASHED'
                        except (AttributeError, TypeError):
                            pass
                        if hasattr(material, "surface_render_method"):
                            try:
                                material.surface_render_method = 'DITHERED'
                            except Exception:
                                pass
                else:
                    # Single diffuse color setup
                    link_socket(diffuse_node_a, "Base Color")
                    
                    if is_material_transparent(mat_name, mat_data):
                        link_socket(diffuse_node_a, "Alpha", "Alpha")
                        try:
                            material.blend_method = 'HASHED'
                        except (AttributeError, TypeError):
                            pass
                        try:
                            material.shadow_method = 'HASHED'
                        except (AttributeError, TypeError):
                            pass
                        if hasattr(material, "surface_render_method"):
                            try:
                                material.surface_render_method = 'DITHERED'
                            except Exception:
                                pass
            else:
                # No diffuse texture loaded
                if diffuse_missing:
                    # Set to bright neon magenta/purple to clearly alert the user that the texture file is missing on disk
                    base_color = (1.0, 0.0, 1.0, 1.0)
                else:
                    # No diffuse texture referenced: set solid color if present in vectors
                    base_color = (0.8, 0.8, 0.8, 1.0)
                    for k, val in vectors.items():
                        k_norm = k.replace(" ", "").replace("_", "").replace("-", "")
                        if k_norm in ("colour", "color", "basecolor", "diffuse", "diffusecolor", "tint"):
                            base_color = (val[0], val[1], val[2], 1.0)
                            break
                shader_node.inputs["Base Color"].default_value = base_color
                
            # 2. Normal Mapping Setup (with Green-channel inversion)
            normal_node_a = load_texture_node(normal_path, "Non-Color")
            normal_node_b = load_texture_node(normal_path_b, "Non-Color")
            
            if normal_node_a:
                normal_map_node = material.node_tree.nodes.new(type="ShaderNodeNormalMap")
                normal_map_node.inputs["Strength"].default_value = 1.0
                link_socket(normal_map_node, "Normal", "Normal")
                
                # Setup inversion and separators
                try:
                    inv_node = material.node_tree.nodes.new(type="ShaderNodeInvertColor")
                except RuntimeError:
                    inv_node = material.node_tree.nodes.new(type="ShaderNodeInvert")
                    
                try:
                    sep_color = material.node_tree.nodes.new(type="ShaderNodeSeparateColor")
                    comb_color = material.node_tree.nodes.new(type="ShaderNodeCombineColor")
                    
                    material.node_tree.links.new(comb_color.inputs["Red"], sep_color.outputs["Red"])
                    material.node_tree.links.new(inv_node.inputs["Color"], sep_color.outputs["Green"])
                    material.node_tree.links.new(comb_color.inputs["Green"], inv_node.outputs["Color"])
                    material.node_tree.links.new(comb_color.inputs["Blue"], sep_color.outputs["Blue"])
                    material.node_tree.links.new(normal_map_node.inputs["Color"], comb_color.outputs["Color"])
                except RuntimeError:
                    sep_color = material.node_tree.nodes.new(type="ShaderNodeSeparateRGB")
                    comb_color = material.node_tree.nodes.new(type="ShaderNodeCombineRGB")
                    
                    material.node_tree.links.new(comb_color.inputs["R"], sep_color.outputs["R"])
                    material.node_tree.links.new(inv_node.inputs["Color"], sep_color.outputs["G"])
                    material.node_tree.links.new(comb_color.inputs["G"], inv_node.outputs["Color"])
                    material.node_tree.links.new(comb_color.inputs["B"], sep_color.outputs["B"])
                    material.node_tree.links.new(normal_map_node.inputs["Color"], comb_color.outputs["Image"])
                    
                if normal_node_b and vcol_node:
                    # Blend normals first
                    mix_node, fac_in, a_in, b_in, out_res = create_mix_node(material)
                    material.node_tree.links.new(mix_node.inputs[fac_in], vcol_node.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[a_in], normal_node_a.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[b_in], normal_node_b.outputs["Color"])
                    
                    if sep_color.type == 'SEPARATE_COLOR':
                        material.node_tree.links.new(sep_color.inputs["Color"], mix_node.outputs[out_res])
                    else:
                        material.node_tree.links.new(sep_color.inputs["Image"], mix_node.outputs[out_res])
                else:
                    if sep_color.type == 'SEPARATE_COLOR':
                        material.node_tree.links.new(sep_color.inputs["Color"], normal_node_a.outputs["Color"])
                    else:
                        material.node_tree.links.new(sep_color.inputs["Image"], normal_node_a.outputs["Color"])
                        
            # 3. Specular Setup
            spec_node = load_texture_node(spec_path, "Non-Color")
            if spec_node:
                link_socket(spec_node, "Specular")
                
            # 4. Roughness Setup
            rough_node_a = load_texture_node(rough_path, "Non-Color")
            rough_node_b = load_texture_node(rough_path_b, "Non-Color")
            
            if rough_node_a:
                if rough_node_b and vcol_node:
                    mix_node, fac_in, a_in, b_in, out_res = create_mix_node(material)
                    material.node_tree.links.new(mix_node.inputs[fac_in], vcol_node.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[a_in], rough_node_a.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[b_in], rough_node_b.outputs["Color"])
                    material.node_tree.links.new(shader_node.inputs["Roughness"], mix_node.outputs[out_res])
                else:
                    link_socket(rough_node_a, "Roughness")
            else:
                # Set solid Roughness from scalars
                for k, val in scalars.items():
                    k_norm = k.replace(" ", "").replace("_", "").replace("-", "")
                    if k_norm in ("roughness", "rough"):
                        shader_node.inputs["Roughness"].default_value = max(0.0, min(1.0, val))
                        break
                        
            # 5. Metallic Setup
            metallic_node_a = load_texture_node(metallic_path, "Non-Color")
            metallic_node_b = load_texture_node(metallic_path_b, "Non-Color")
            
            if metallic_node_a:
                if metallic_node_b and vcol_node:
                    mix_node, fac_in, a_in, b_in, out_res = create_mix_node(material)
                    material.node_tree.links.new(mix_node.inputs[fac_in], vcol_node.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[a_in], metallic_node_a.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[b_in], metallic_node_b.outputs["Color"])
                    material.node_tree.links.new(shader_node.inputs["Metallic"], mix_node.outputs[out_res])
                else:
                    link_socket(metallic_node_a, "Metallic")
            else:
                # Set solid Metallic from scalars
                for k, val in scalars.items():
                    k_norm = k.replace(" ", "").replace("_", "").replace("-", "")
                    if k_norm in ("metallic", "metal"):
                        shader_node.inputs["Metallic"].default_value = max(0.0, min(1.0, val))
                        break
                    
            # 6. MRA packed mask setup (R=AO, G=Roughness, B=Metallic)
            mask_node_a = load_texture_node(mask_path, "Non-Color") if (mask_path and not rough_path and not metallic_path) else None
            mask_node_b = load_texture_node(mask_path_b, "Non-Color") if (mask_path_b and not rough_path_b and not metallic_path_b) else None
            
            if (mask_node_a or mask_node_b) and not rough_node_a and not metallic_node_a:
                def setup_mask_links(m_node):
                    try:
                        sep = material.node_tree.nodes.new(type="ShaderNodeSeparateColor")
                        material.node_tree.links.new(sep.inputs["Color"], m_node.outputs["Color"])
                        return sep.outputs["Green"], sep.outputs["Blue"]
                    except RuntimeError:
                        sep = material.node_tree.nodes.new(type="ShaderNodeSeparateRGB")
                        material.node_tree.links.new(sep.inputs["Image"], m_node.outputs["Color"])
                        return sep.outputs["G"], sep.outputs["B"]
                        
                if mask_node_a and mask_node_b and vcol_node:
                    # Blend masks first
                    mix_node, fac_in, a_in, b_in, out_res = create_mix_node(material)
                    material.node_tree.links.new(mix_node.inputs[fac_in], vcol_node.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[a_in], mask_node_a.outputs["Color"])
                    material.node_tree.links.new(mix_node.inputs[b_in], mask_node_b.outputs["Color"])
                    
                    try:
                        sep = material.node_tree.nodes.new(type="ShaderNodeSeparateColor")
                        material.node_tree.links.new(sep.inputs["Color"], mix_node.outputs[out_res])
                        material.node_tree.links.new(shader_node.inputs["Roughness"], sep.outputs["Green"])
                        material.node_tree.links.new(shader_node.inputs["Metallic"], sep.outputs["Blue"])
                    except RuntimeError:
                        sep = material.node_tree.nodes.new(type="ShaderNodeSeparateRGB")
                        material.node_tree.links.new(sep.inputs["Image"], mix_node.outputs[out_res])
                        material.node_tree.links.new(shader_node.inputs["Roughness"], sep.outputs["G"])
                        material.node_tree.links.new(shader_node.inputs["Metallic"], sep.outputs["B"])
                elif mask_node_a:
                    r_out, b_out = setup_mask_links(mask_node_a)
                    material.node_tree.links.new(shader_node.inputs["Roughness"], r_out)
                    material.node_tree.links.new(shader_node.inputs["Metallic"], b_out)
                    
            # 7. Emissive Shading Setup
            emissive_strength = 1.0
            for k, val in scalars.items():
                if k in ("emissivemult", "emissivestrength", "emissive_multiplier", "emissive_strength", "emissive multiplier", "emissive strength"):
                    emissive_strength = val
                    break
                    
            emissive_color = None
            for k, val in vectors.items():
                if k in ("emissive", "emissivecolor", "emissive_color", "emissive color"):
                    emissive_color = val
                    break
                    
            emissive_node = load_texture_node(emissive_path, "sRGB")
            emission_socket_name = "Emission Color"
            emission_socket = shader_node.inputs.get(emission_socket_name)
            if emission_socket is None:
                emission_socket = shader_node.inputs.get("Emission")
                
            if emissive_node:
                link_socket(emissive_node, "Emission Color")
                strength_socket = shader_node.inputs.get("Emission Strength")
                if strength_socket is not None:
                    strength_socket.default_value = emissive_strength
            elif emissive_color:
                if emission_socket is not None:
                    emission_socket.default_value = (emissive_color[0], emissive_color[1], emissive_color[2], 1.0)
                strength_socket = shader_node.inputs.get("Emission Strength")
                if strength_socket is not None:
                    strength_socket.default_value = emissive_strength
                    
        print("Material configuration complete.")
        return {'FINISHED'}

class ExportMissingAssetsListOperator(bpy.types.Operator):
    bl_idname = "lis.export_missing_assets"
    bl_label = "Export Referenced Assets List"
    bl_description = "Export a detailed list of missing and referenced assets to a text file next to the JSON"
    
    def execute(self, context):
        mytool = context.scene.my_tool
        json_path = mytool.json_file
        if not json_path or not os.path.exists(json_path):
            self.report({'ERROR'}, "Please select a UMAP JSON file first")
            return {'CANCELLED'}
            
        base_dir = mytool.base_directory
        file_index = None
        if base_dir and os.path.exists(base_dir):
            file_index = get_cached_index(base_dir)
            
        referenced_assets = get_all_referenced_assets(json_path, base_dir, file_index)
        if not referenced_assets:
            self.report({'WARNING'}, "No assets found to export")
            return {'CANCELLED'}
            
        output_dir = os.path.dirname(json_path)
        output_file = os.path.join(output_dir, "referenced_assets.txt")
        
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write("=== UMAP LEVEL REFERENCED ASSETS REPORT ===\n")
                f.write(f"Level JSON: {json_path}\n")
                f.write(f"Assets Directory: {base_dir if base_dir else 'None'}\n\n")
                
                # Separate into missing and found
                missing_meshes = []
                found_meshes = []
                missing_mats = []
                found_mats = []
                missing_levels = []
                found_levels = []
                
                for asset_type, ue_path in sorted(referenced_assets, key=lambda x: x[1]):
                    clean_path = clean_unreal_path(ue_path)
                    found = False
                    if asset_type == "mesh" and is_basic_shape(ue_path):
                        found = True
                    elif asset_type == "material" and is_basic_shape_material(ue_path):
                        found = True
                    elif file_index:
                        if asset_type == "mesh":
                            preferred = {'.gltf', '.glb', '.fbx', '.obj'}
                        elif asset_type == "level":
                            preferred = {'.json'}
                        else:
                            preferred = {'.json', '.mat'}
                        found_path = resolve_file_path(file_index, clean_path, preferred_exts=preferred)
                        if found_path:
                            found = True
                            
                    if asset_type == "mesh":
                        if found:
                            found_meshes.append(ue_path)
                        else:
                            missing_meshes.append(ue_path)
                    elif asset_type == "level":
                        if found:
                            found_levels.append(ue_path)
                        else:
                            missing_levels.append(ue_path)
                    else:
                        if found:
                            found_mats.append(ue_path)
                        else:
                            missing_mats.append(ue_path)
                            
                f.write(f"--- MISSING SUB-LEVELS ({len(missing_levels)}) ---\n")
                for path in missing_levels:
                    f.write(f"{path}\n")
                f.write("\n")
                
                f.write(f"--- MISSING MESHES ({len(missing_meshes)}) ---\n")
                for path in missing_meshes:
                    f.write(f"{path}\n")
                f.write("\n")
                
                f.write(f"--- MISSING MATERIALS/DECALS ({len(missing_mats)}) ---\n")
                for path in missing_mats:
                    f.write(f"{path}\n")
                f.write("\n")
                
                f.write(f"--- FOUND SUB-LEVELS ({len(found_levels)}) ---\n")
                for path in found_levels:
                    f.write(f"{path}\n")
                f.write("\n")
                
                f.write(f"--- FOUND MESHES ({len(found_meshes)}) ---\n")
                for path in found_meshes:
                    f.write(f"{path}\n")
                f.write("\n")
                
                f.write(f"--- FOUND MATERIALS/DECALS ({len(found_mats)}) ---\n")
                for path in found_mats:
                    f.write(f"{path}\n")
                f.write("\n")
                
            self.report({'INFO'}, f"Saved report to: {output_file}")
            try:
                os.startfile(output_file)
            except Exception:
                pass
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save report: {e}")
            return {'CANCELLED'}

class RefreshAnalysisOperator(bpy.types.Operator):
    bl_idname = "lis.refresh_analysis"
    bl_label = "Refresh Asset Analysis"
    bl_description = "Rescan the asset directory and refresh the missing assets list"

    def execute(self, context):
        mytool = context.scene.my_tool
        base_dir = mytool.base_directory
        if base_dir:
            norm_dir = os.path.abspath(base_dir).lower()
            global _index_cache, _resolve_cache, _sorted_keys_cache
            if norm_dir in _index_cache:
                del _index_cache[norm_dir]
            _resolve_cache.clear()
            _sorted_keys_cache.clear()
        try:
            run_asset_analysis(mytool)
            self.report({'INFO'}, "Asset analysis refreshed.")
        except Exception as e:
            self.report({'ERROR'}, f"Refresh failed: {e}")
        return {'FINISHED'}

class AutoExtractAssetsOperator(bpy.types.Operator):
    bl_idname = "lis.auto_extract_assets"
    bl_label = "Auto-Extract Assets from Game"
    bl_description = "Automatically extracts missing assets from game containers using CUE4Parse"
    
    _timer = None
    _process = None
    _missing_temp_file = None
    _output_queue = None
    _output_thread = None
    
    def modal(self, context, event):
        if event.type == 'TIMER':
            # Drain the output queue (non-blocking, filled by reader thread)
            import queue as _queue
            while True:
                try:
                    line = self._output_queue.get_nowait()
                    print(f"[UE Extractor] {line}")
                except _queue.Empty:
                    break
                    
            ret = self._process.poll()
            if ret is not None:
                # Wait for reader thread to finish and drain remaining output
                if self._output_thread:
                    self._output_thread.join(timeout=2.0)
                import queue as _queue
                while True:
                    try:
                        line = self._output_queue.get_nowait()
                        print(f"[UE Extractor] {line}")
                    except _queue.Empty:
                        break
                self.report_cleanup(context)
                if ret == 0:
                    self.report({'INFO'}, "Extraction completed! Check Window > Toggle System Console for details.")
                    try:
                        run_asset_analysis(context.scene.my_tool)
                    except Exception as e:
                        print(f"Error refreshing analysis: {e}")
                else:
                    self.report({'ERROR'}, f"Extraction failed (exit code {ret}). Open Window > Toggle System Console for details.")
                return {'FINISHED'}
                
        return {'PASS_THROUGH'}
        
    def report_cleanup(self, context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None
        self._process = None
        self._output_thread = None
        self._output_queue = None
        
        context.scene.my_tool.import_progress = 0.0
        context.scene.my_tool.import_status = "Idle"
        
        if self._missing_temp_file and os.path.exists(self._missing_temp_file):
            try:
                os.remove(self._missing_temp_file)
            except Exception as e:
                print(f"Failed to remove temp file {self._missing_temp_file}: {e}")
                
    def execute(self, context):
        import subprocess
        mytool = context.scene.my_tool
        
        json_path = mytool.json_file
        base_dir = mytool.base_directory
        paks_dir = mytool.game_paks_path
        aes_key = mytool.game_aes_key
        usmap_path = mytool.game_usmap_path
        
        if not json_path or not os.path.exists(json_path):
            self.report({'ERROR'}, "Please select a UMAP JSON file first")
            return {'CANCELLED'}
            
        if not base_dir or not os.path.exists(base_dir):
            self.report({'ERROR'}, "Please select an Assets Base Directory")
            return {'CANCELLED'}
            
        if not paks_dir or not os.path.exists(paks_dir):
            self.report({'ERROR'}, "Please select the Game Paks Directory")
            return {'CANCELLED'}
            
        file_index = get_cached_index(base_dir)
        disabled_sublevels = {item.package_path for item in mytool.sublevels if not item.enabled}
        referenced_assets = get_all_referenced_assets(json_path, base_dir, file_index, disabled_sublevels)
        
        missing_list = []
        for asset_type, ue_path in referenced_assets:
            clean_path = clean_unreal_path(ue_path)
            if not clean_path:
                continue
                
            found = False
            if asset_type == "mesh" and is_basic_shape(ue_path):
                found = True
            elif asset_type == "material" and is_basic_shape_material(ue_path):
                found = True
            elif file_index:
                if asset_type == "mesh":
                    preferred = {'.gltf', '.glb', '.fbx', '.obj'}
                elif asset_type == "level":
                    preferred = {'.json'}
                else:
                    preferred = {'.json', '.mat'}
                found_path = resolve_file_path(file_index, clean_path, preferred_exts=preferred)
                if found_path:
                    found = True
                    
            if not found:
                missing_list.append(ue_path)
                
        print(f"[AutoExtract] Total referenced assets: {len(list(referenced_assets.__class__.__mro__))}")
        print(f"[AutoExtract] Missing assets count: {len(missing_list)}")
        for i, p in enumerate(missing_list[:10]):
            print(f"[AutoExtract]   [{i}] {p}")
        if len(missing_list) > 10:
            print(f"[AutoExtract]   ... and {len(missing_list)-10} more")
                
        if not missing_list:
            self.report({'INFO'}, "No missing assets to extract — all assets already present in Asset Dir.")
            return {'FINISHED'}
            
        self._missing_temp_file = os.path.join(os.path.dirname(json_path), "missing_assets_temp.json")
        try:
            with open(self._missing_temp_file, 'w', encoding='utf-8') as f:
                json.dump(missing_list, f, indent=4)
        except Exception as e:
            self.report({'ERROR'}, f"Failed to create temporary list file: {e}")
            return {'CANCELLED'}
            
        addon_dir = os.path.dirname(os.path.abspath(__file__))
        extractor_path = os.path.join(addon_dir, "ue_extractor.exe")
        
        if not os.path.exists(extractor_path):
            self.report({'ERROR'}, f"ue_extractor.exe not found!\nExpected next to blender_umap_importer.py:\n{extractor_path}")
            if os.path.exists(self._missing_temp_file):
                os.remove(self._missing_temp_file)
            return {'CANCELLED'}
                
        cmd = [
            extractor_path,
            "--paks", paks_dir,
            "--list", self._missing_temp_file,
            "--out", base_dir
        ]
        if aes_key:
            cmd.extend(["--key", aes_key])
        if usmap_path and os.path.exists(usmap_path):
            cmd.extend(["--mappings", usmap_path])
            
        print(f"Running extractor command: {' '.join(cmd)}")
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
        except Exception as e:
            self.report({'ERROR'}, f"Failed to launch extractor: {e}")
            if os.path.exists(self._missing_temp_file):
                os.remove(self._missing_temp_file)
            return {'CANCELLED'}
        
        # Start background thread to read stdout without blocking
        import queue, threading
        self._output_queue = queue.Queue()
        def _reader(proc, q):
            try:
                for line in proc.stdout:
                    q.put(line.rstrip())
            except Exception:
                pass
        self._output_thread = threading.Thread(target=_reader, args=(self._process, self._output_queue), daemon=True)
        self._output_thread.start()
            
        mytool.import_status = "Extracting assets from game files..."
        mytool.import_progress = 50.0
        
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

classes = (
    LIS_ReferencedFolderItem,
    LIS_UL_referenced_folders,
    LIS_SubLevelItem,
    LIS_UL_sublevels,
    ShowMissingInfoOperator,
    MISettings,
    VIEW3D_PT_map_importer_panel,
    MapImporter,
    MaterialImporter,
    ExportMissingAssetsListOperator,
    RefreshAnalysisOperator,
    AutoExtractAssetsOperator,
)

def register():
    bpy.types.WindowManager.progress = bpy.props.FloatProperty()
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            pass
    bpy.types.Scene.my_tool = bpy.props.PointerProperty(type=MISettings)

def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    try:
        del bpy.types.Scene.my_tool
    except Exception:
        pass

if __name__ == "__main__":
    register()

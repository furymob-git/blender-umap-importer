bl_info = {
    "name": "UMAP Importer",
    "author": "FURYMOB & Gemini",
    "version": (1, 2, 0),
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
        run_asset_analysis(self)
    except Exception as e:
        print(f"Error in update_analysis: {e}")


class LIS_ReferencedFolderItem(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty()
    missing: bpy.props.IntProperty()
    missing_meshes: bpy.props.IntProperty()
    missing_materials: bpy.props.IntProperty()
    missing_mesh_names: bpy.props.StringProperty()      # newline-separated, max 15
    missing_material_names: bpy.props.StringProperty()  # newline-separated, max 15
    total: bpy.props.IntProperty()

class LIS_UL_referenced_folders(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            if item.missing > 0:
                split = row.split(factor=0.75, align=True)
                split.label(text=item.path, icon="FOLDER_REDIRECT")
                badge_row = split.row(align=True)
                badge_row.alignment = 'RIGHT'
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
        
        self.layout.prop(mytool, "json_file")
        self.layout.prop(mytool, "base_directory")
        self.layout.prop(mytool, "disable_viewport_refresh")
        self.layout.prop(mytool, "use_smart_resolve")
        self.layout.prop(mytool, "hide_collisions")
        self.layout.prop(mytool, "skip_missing_assets")
        self.layout.prop(mytool, "skip_missing_materials")
        
        row = self.layout.row()
        row.operator(MapImporter.bl_idname, text="Import Level")
        
        if mytool.import_progress > 0.0 and mytool.import_progress < 100.0:
            box = self.layout.box()
            box.label(text=mytool.import_status, icon="INFO")
            row_prog = box.row()
            row_prog.prop(mytool, "import_progress", text="Progress", slider=True)
            row_prog.enabled = False
            box.label(text="Press ESC in 3D View to cancel", icon="CANCEL")
        elif mytool.import_progress == 100.0:
            box = self.layout.box()
            box.label(text="Import completed successfully!", icon="CHECKMARK")
        
        self.layout.separator()
        
        # Collapsible Analysis Section
        box = self.layout.box()
        box.prop(mytool, "show_analysis", text="Asset Analysis / Missing Folders", icon="FILE_TEXT", toggle=True)
        if mytool.show_analysis:
            box.label(text=_analysis_results["status"], icon="INFO")
            
            row = box.row(align=True)
            row.prop(mytool, "analysis_depth", text="Folder Depth")
            row.operator("lis.refresh_analysis", text="", icon="FILE_REFRESH")

            
            if _analysis_results["folders"]:
                box.prop(mytool, "analysis_mode", expand=True)
                
                if len(mytool.analysis_folders) == 0:
                    box.label(text="No folders found.", icon="CHECKMARK")
                else:
                    box.template_list(
                        "LIS_UL_referenced_folders", "",
                        mytool, "analysis_folders",
                        mytool, "analysis_folders_index",
                        rows=8
                    )
                
                box.separator()
                box.operator("lis.export_missing_assets", text="Export Detailed List to Text File", icon="EXPORT")

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
                candidates.extend(index[key])
                idx += 1
            else:
                break
                
        if candidates:
            candidates.sort()  # Sort alphabetically to be deterministic and pick the lowest/first variant
            if preferred_exts:
                filtered = [c for c in candidates if os.path.splitext(c)[1].lower() in preferred_exts]
                if filtered:
                    print(f"Warning: Exact asset '{ue_path}' not found. Using smart fallback ({desc}): '{os.path.basename(filtered[0])}'")
                    return filtered[0]
            fallback_path = candidates[0]
            print(f"Warning: Exact asset '{ue_path}' not found. Using smart fallback ({desc}): '{os.path.basename(fallback_path)}'")
            return fallback_path

    return None


_blueprint_mesh_cache = {}
_blueprint_properties_cache = {}

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
        
    parts = template_path.split('.')
    bp_path = parts[0]
    try:
        suffix_idx = int(parts[-1])
    except ValueError:
        suffix_idx = None
        
    bp_json_path = resolve_file_path(index, bp_path, preferred_exts={'.json'})
    if not bp_json_path or not os.path.exists(bp_json_path):
        _blueprint_properties_cache[template_path] = {}
        return {}
        
    try:
        with open(bp_json_path, 'r', encoding='utf-8') as f:
            bp_data = json.load(f)
            
        if not isinstance(bp_data, list):
            bp_data = [bp_data]
            
        obj = None
        if suffix_idx is not None and suffix_idx < len(bp_data):
            obj = bp_data[suffix_idx]
        else:
            # Fallback: search by name
            obj_name = template_path.split(':')[-1] if ':' in template_path else parts[-1]
            for item in bp_data:
                if isinstance(item, dict) and item.get("Name") == obj_name:
                    obj = item
                    break
            if not obj:
                # If still not found, check if ObjectName is inside the Name
                for item in bp_data:
                    if isinstance(item, dict) and obj_name in item.get("Name", ""):
                        obj = item
                        break
                        
        if obj and isinstance(obj, dict):
            props = obj.get("Properties", {})
            _blueprint_properties_cache[template_path] = props
            return props
    except Exception as e:
        print(f"Error parsing blueprint template properties for {bp_json_path}: {e}")
        
    _blueprint_properties_cache[template_path] = {}
    return {}

def resolve_blueprint_mesh(index, template_path):
    """
    Loads and parses a blueprint class template JSON to find the default
    StaticMesh or SkeletalMesh path if it is null or missing in the UMAP level JSON.
    """
    if not template_path:
        return None
        
    global _blueprint_mesh_cache
    if template_path in _blueprint_mesh_cache:
        return _blueprint_mesh_cache[template_path]
        
    props = resolve_blueprint_properties(index, template_path)
    if props:
        sm_ref = props.get("StaticMesh") or props.get("SkeletalMesh")
        if sm_ref and isinstance(sm_ref, dict):
            res = sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
            _blueprint_mesh_cache[template_path] = res
            return res
            
    # Fallback: search for first object with a StaticMesh or SkeletalMesh property
    # in the whole blueprint data list
    parts = template_path.split('.')
    bp_path = parts[0]
    bp_json_path = resolve_file_path(index, bp_path, preferred_exts={'.json'})
    if bp_json_path and os.path.exists(bp_json_path):
        try:
            with open(bp_json_path, 'r', encoding='utf-8') as f:
                bp_data = json.load(f)
            if not isinstance(bp_data, list):
                bp_data = [bp_data]
            for item in bp_data:
                if isinstance(item, dict):
                    p = item.get("Properties", {})
                    sm_ref = p.get("StaticMesh") or p.get("SkeletalMesh")
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

def get_all_referenced_assets(json_filepath, base_dir=None, file_index=None):
    """
    Scans a level JSON and all resolved Blueprint templates to extract all
    referenced meshes and materials.
    """
    referenced = set()
    if not json_filepath or not os.path.exists(json_filepath):
        return referenced
        
    try:
        with open(json_filepath, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
            
        if not isinstance(json_data, list):
            json_data = [json_data]
            
        for entity in json_data:
            props = entity.get("Properties", {})
            etype = entity.get("Type", "")
            
            # 1. Mesh components
            if etype in ("StaticMeshComponent", "InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent", "SkeletalMeshComponent"):
                sm_ref = props.get("StaticMesh") or props.get("SkeletalMesh")
                if sm_ref:
                    mesh_path = sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
                    if mesh_path:
                        referenced.add(("mesh", mesh_path))
                else:
                    # Blueprint template
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
                        
    except Exception as e:
        print(f"Error gathering referenced assets: {e}")
        
    return referenced

def run_asset_analysis(mytool):
    """
    Processes all level JSON assets, verifies existence, and groups missing/total counts by folder.
    """
    global _analysis_results, _resolve_cache
    _resolve_cache.clear()
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
        
    referenced_assets = get_all_referenced_assets(json_path, base_dir, file_index)
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
            folders_data[folder] = {"missing": 0, "missing_meshes": 0, "missing_materials": 0,
                                    "missing_mesh_names": [], "missing_material_names": [], "total": 0}
            
        folders_data[folder]["total"] += 1
        
        found = False
        if asset_type == "mesh" and is_basic_shape(ue_path):
            found = True
        elif asset_type == "material" and is_basic_shape_material(ue_path):
            found = True
        elif file_index:
            preferred = {'.gltf', '.glb', '.fbx', '.obj'} if asset_type == "mesh" else {'.json', '.mat'}
            found_path = resolve_file_path(file_index, clean_path, preferred_exts=preferred)
            if found_path:
                found = True
                
        if not found:
            folders_data[folder]["missing"] += 1
            asset_name = parts[-1] if parts else clean_path
            if asset_type == "mesh":
                folders_data[folder]["missing_meshes"] += 1
                folders_data[folder]["missing_mesh_names"].append(asset_name)
            else:
                folders_data[folder]["missing_materials"] += 1
                folders_data[folder]["missing_material_names"].append(asset_name)
            missing_count += 1
            
    folders_list = []
    for f_path, data in folders_data.items():
        # Cap names at 15 entries for tooltip readability
        mesh_names = data["missing_mesh_names"][:15]
        mat_names = data["missing_material_names"][:15]
        if len(data["missing_mesh_names"]) > 15:
            mesh_names.append(f"... and {len(data['missing_mesh_names']) - 15} more")
        if len(data["missing_material_names"]) > 15:
            mat_names.append(f"... and {len(data['missing_material_names']) - 15} more")
        folders_list.append({
            "path": f_path,
            "missing": data["missing"],
            "missing_meshes": data["missing_meshes"],
            "missing_materials": data["missing_materials"],
            "missing_mesh_names": "\n".join(mesh_names),
            "missing_material_names": "\n".join(mat_names),
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
        item.missing_mesh_names = f["missing_mesh_names"]
        item.missing_material_names = f["missing_material_names"]
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
                bpy.ops.wm.gltf_import(filepath=filepath)
            else:
                bpy.ops.import_scene.gltf(filepath=filepath)
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
            
    if len(roots) == 1:
        root_obj = roots[0]
        root_obj.name = name
        return root_obj
    else:
        # Multiple root objects: group under a single parent empty wrapper
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
                
        # Reset progress values
        context.scene.my_tool.import_progress = 0.0
        context.scene.my_tool.import_status = "Idle"

    def import_generator(self, context):
        # Clear caches for a fresh import
        global _blueprint_mesh_cache, _blueprint_properties_cache, _resolve_cache
        _blueprint_mesh_cache.clear()
        _blueprint_properties_cache.clear()
        _resolve_cache.clear()

        scene = context.scene
        mytool = scene.my_tool
        
        base_dir = mytool.base_directory
        map_json = mytool.json_file
        hide_collisions = mytool.hide_collisions
        skip_missing_assets = mytool.skip_missing_assets
        skip_missing_materials = mytool.skip_missing_materials
        
        # 1. Directory indexing
        mytool.import_status = "Scanning asset directory..."
        mytool.import_progress = 5.0
        yield False
        
        file_index = get_cached_index(base_dir)
        mytool.import_progress = 10.0
        yield False
        
        # 2. Parse level JSON
        mytool.import_status = "Parsing level JSON..."
        yield False
        
        with open(map_json, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
            
        if not isinstance(json_data, list):
            json_data = [json_data]
            
        total_objects = len(json_data)
        
        # Create map collection
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
        parent_relations = {} # child index -> parent index
        decal_objs = []
        
        # Pass 1: Instantiation of all Actors and Components
        last_yield_time = time.time()
        time_slice = 0.2 if mytool.disable_viewport_refresh else 0.015
        
        for i, entity in enumerate(json_data):
            # Time-slicing: yield to Blender so it updates the UI/viewport
            if time.time() - last_yield_time > time_slice:
                mytool.import_status = f"Importing components ({i}/{total_objects})..."
                mytool.import_progress = 10.0 + (i / total_objects) * 70.0 # scale loop to 10% - 80% range
                yield False
                last_yield_time = time.time()
                
            etype = entity.get("Type", "")
            ename = entity.get("Name", f"Obj_{i}")
            
            # Determine if it's an Actor (top-level) or a Component
            outer_dict = entity.get("Outer")
            is_actor = False
            outer_index = None
            if outer_dict:
                outer_name = outer_dict.get("ObjectName", "")
                outer_path = outer_dict.get("ObjectPath", "")
                if outer_name.startswith("Level'"):
                    is_actor = True
                else:
                    parts = outer_path.split('.')
                    if len(parts) > 1:
                        try:
                            outer_index = int(parts[-1])
                        except ValueError:
                            pass
            else:
                is_actor = True
                
            props = entity.get("Properties", {})
            
            # Resolve blueprint template properties if Template is present
            template_ref = entity.get("Template")
            if template_ref and file_index:
                template_path = template_ref.get("ObjectPath")
                if template_path:
                    template_props = resolve_blueprint_properties(file_index, template_path)
                    # Merge properties: template properties are overridden by instance properties
                    props = {**template_props, **props}
            
            if is_actor:
                # Actors get represented as Blender Empties to group their components
                label = entity.get("ActorLabel", ename)
                actor_empty = bpy.data.objects.new(name=label, object_data=None)
                import_collection.objects.link(actor_empty)
                blender_objs[i] = actor_empty
                local_matrices[i] = mathutils.Matrix.Identity(4)
                continue
                
            # Retrieve component transform
            loc = props.get("RelativeLocation", {})
            rot = props.get("RelativeRotation", {})
            scale = props.get("RelativeScale3D", {})
            local_matrices[i] = get_blender_transform(loc, rot, scale)
            
            # Parent configuration mapping
            attach_parent = props.get("AttachParent")
            if attach_parent:
                parts = attach_parent.get("ObjectPath", "").split('.')
                if len(parts) > 1:
                    try:
                        parent_relations[i] = int(parts[-1])
                    except ValueError:
                        pass
            elif outer_index is not None:
                parent_relations[i] = outer_index
                
            # Skip component if missing overridden materials and toggle is on
            if skip_missing_materials and should_skip_component_due_to_missing_materials(etype, props, file_index):
                print(f"Skipping component {ename} because its overridden materials are missing from disk.")
                continue
                
            # Parse component categories
            if etype in ("StaticMeshComponent", "InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent", "SkeletalMeshComponent"):
                sm_ref = props.get("StaticMesh") or props.get("SkeletalMesh")
                mesh_path = None
                if sm_ref:
                    mesh_path = sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
                else:
                    # Try resolving static/skeletal mesh from template blueprint JSON
                    template_ref = entity.get("Template")
                    if template_ref:
                        template_path = template_ref.get("ObjectPath")
                        mesh_path = resolve_blueprint_mesh(file_index, template_path)
                        
                if not mesh_path:
                    # Fallback to Empty SceneComponent
                    if not skip_missing_assets:
                        empty_obj = bpy.data.objects.new(name=ename, object_data=None)
                        import_collection.objects.link(empty_obj)
                        blender_objs[i] = empty_obj
                    continue
                    
                # Support instanced static meshes (ISM / HISM / Foliage)
                instances = entity.get("PerInstanceSMData")
                if not instances and "Properties" in entity:
                    instances = entity.get("Properties", {}).get("PerInstanceSMData")
                    
                if instances and etype in ("HierarchicalInstancedStaticMeshComponent", "InstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent"):
                    print(f"Creating Instanced Component {ename} ({len(instances)} instances)")
                    comp_empty = bpy.data.objects.new(name=ename, object_data=None)
                    import_collection.objects.link(comp_empty)
                    blender_objs[i] = comp_empty
                    
                    # Cache/import the template mesh
                    base_mesh = None
                    if mesh_path in mesh_cache:
                        base_mesh = mesh_cache[mesh_path]
                    else:
                        if is_basic_shape(mesh_path):
                            base_mesh = create_basic_shape(mesh_path, f"{ename}_Template", import_collection)
                            if base_mesh:
                                mesh_cache[mesh_path] = base_mesh
                                base_mesh.hide_viewport = True
                                base_mesh.hide_render = True
                        else:
                            mesh_file = resolve_file_path(file_index, mesh_path, preferred_exts={'.gltf', '.glb', '.fbx', '.obj'})
                            if mesh_file:
                                base_mesh = import_asset(mesh_file, f"{ename}_Template", import_collection, hide_collisions)
                                if base_mesh:
                                    mesh_cache[mesh_path] = base_mesh
                                    base_mesh.hide_viewport = True
                                    base_mesh.hide_render = True
                                
                    if not base_mesh:
                        print(f"Asset file not found or failed to import for: {mesh_path}")
                        continue
                        
                    if base_mesh:
                        for inst_idx, inst in enumerate(instances):
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
                    # Single mesh import logic
                    mesh_obj = None
                    if mesh_path in mesh_cache:
                        mesh_obj = clone_hierarchy(mesh_cache[mesh_path], ename, import_collection, unhide=True, hide_collisions=hide_collisions)
                    else:
                        if is_basic_shape(mesh_path):
                            mesh_obj = create_basic_shape(mesh_path, ename, import_collection)
                            if mesh_obj:
                                mesh_cache[mesh_path] = mesh_obj
                                # Apply OverrideMaterials from props onto the generated shape
                                override_mats = props.get("OverrideMaterials", [])
                                if override_mats and mesh_obj.type == 'MESH':
                                    mesh_obj.data.materials.clear()
                                    for mat_ref in override_mats:
                                        if mat_ref:
                                            mat_path = mat_ref.get("ObjectPath") or mat_ref.get("ObjectName") or ""
                                            mat_clean = mat_path.split("'")[1] if "'" in mat_path else mat_path
                                            mat_clean = mat_clean.split(".")[0].split("/")[-1]
                                            mat = bpy.data.materials.get(mat_clean)
                                            if not mat:
                                                mat = bpy.data.materials.new(name=mat_clean)
                                                mat.use_nodes = True
                                            mesh_obj.data.materials.append(mat)
                        else:
                            mesh_file = resolve_file_path(file_index, mesh_path, preferred_exts={'.gltf', '.glb', '.fbx', '.obj'})
                            if mesh_file:
                                mesh_obj = import_asset(mesh_file, ename, import_collection, hide_collisions)
                                if mesh_obj:
                                    mesh_cache[mesh_path] = mesh_obj
                                
                    if mesh_obj:
                        blender_objs[i] = mesh_obj
                    else:
                        if not skip_missing_assets:
                            empty_obj = bpy.data.objects.new(name=ename, object_data=None)
                            import_collection.objects.link(empty_obj)
                            blender_objs[i] = empty_obj
                        
            # Light Components
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
                blender_objs[i] = light_obj
                
            # Decal Projection Support
            elif etype == "DecalComponent":
                mat_ref = props.get("DecalMaterial")
                mat_name = None
                if mat_ref:
                    obj_name = mat_ref.get("ObjectName", "")
                    mat_name = obj_name.split("'")[1] if "'" in obj_name else obj_name
                
                material_obj = None
                if mat_name:
                    material_obj = bpy.data.materials.get(mat_name)
                    if not material_obj:
                        material_obj = bpy.data.materials.new(name=mat_name)
                        material_obj.use_nodes = True
                        
                decal_obj = create_decal_plane_mesh(ename, material_obj, import_collection)
                blender_objs[i] = decal_obj
                decal_objs.append((decal_obj, props))
                
                # Apply DecalSize if present in properties
                decal_size = props.get("DecalSize", {})
                if decal_size:
                    ds_x = decal_size.get("X", 128.0) / 128.0
                    ds_y = decal_size.get("Y", 128.0) / 128.0
                    ds_z = decal_size.get("Z", 128.0) / 128.0
                    scale_decal_mat = mathutils.Matrix.Diagonal((ds_x, ds_y, ds_z, 1.0))
                    local_matrices[i] = local_matrices[i] @ scale_decal_mat
                
            # Default component wrapper (SceneComponent)
            else:
                empty_obj = bpy.data.objects.new(name=ename, object_data=None)
                import_collection.objects.link(empty_obj)
                blender_objs[i] = empty_obj
                
        # Pass 2: Parenting Links
        mytool.import_status = "Establishing parenting structures..."
        mytool.import_progress = 85.0
        yield False
        
        for i, obj in blender_objs.items():
            if i in parent_relations:
                p_idx = parent_relations[i]
                if p_idx in blender_objs:
                    obj.parent = blender_objs[p_idx]
                    
        # Clean up empty wrappers if skip_missing_assets is enabled
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
                
            empties = [(i, obj) for i, obj in blender_objs.items() if obj.type == 'EMPTY']
            empties.sort(key=lambda x: get_depth(x[1]), reverse=True)
            
            for i, obj in empties:
                if not obj.children:
                    if obj.name in import_collection.objects:
                        import_collection.objects.unlink(obj)
                    bpy.data.objects.remove(obj, do_unlink=True)
                    del blender_objs[i]
                    if i in local_matrices:
                        del local_matrices[i]
                    
        # Pass 3: Apply transforms
        mytool.import_status = "Assigning transformations..."
        mytool.import_progress = 95.0
        yield False
        
        for i, obj in blender_objs.items():
            if i in local_matrices:
                obj.matrix_basis = local_matrices[i]
                
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
                    
        # Material rebuilds
        print("Configuring material shaders...")
        for material in list(materials):
            mat_name = material.name
            
            if 'WorldGridMaterial' in mat_name:
                continue
                
            mat_file = resolve_file_path(file_index, mat_name, preferred_exts={'.json', '.mat'})
            if not mat_file:
                if mat_name.lower() == "basicshapematerial":
                    # Build a procedural checker grid material
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
                    
                    # Add Checker Texture
                    checker = nodes.new(type="ShaderNodeTexChecker")
                    checker.inputs["Color1"].default_value = (0.4, 0.4, 0.4, 1.0) # Dark grey
                    checker.inputs["Color2"].default_value = (0.5, 0.5, 0.5, 1.0) # Light grey
                    checker.inputs["Scale"].default_value = 10.0 # Scale of checker grid
                    
                    # Connect Checker Color to Principled Base Color
                    links.new(shader_node.inputs["Base Color"], checker.outputs["Color"])
                    
                    # Set standard roughness
                    shader_node.inputs["Roughness"].default_value = 0.5
                    print("Generated default procedural grid for BasicShapeMaterial")
                    continue
                else:
                    print(f"Material file not found for: {mat_name}")
                    continue
                
            textures = {}
            scalars = {}
            vectors = {}
            mat_data = None
            
            def add_tex(k, v):
                if not k or not v:
                    return
                k_lower = k.lower()
                textures[k_lower] = v
                k_norm = k_lower.replace(" ", "").replace("_", "").replace("-", "")
                if k_norm not in textures:
                    textures[k_norm] = v
                    
            if mat_file.endswith('.json'):
                try:
                    with open(mat_file, 'r', encoding='utf-8') as f:
                        mat_data = json.load(f)
                        
                    def extract_all_textures(data_item):
                        if isinstance(data_item, dict):
                            # Clean up period extensions inside Unreal names
                            if "Textures" in data_item and isinstance(data_item["Textures"], dict):
                                for k, v in data_item["Textures"].items():
                                    if isinstance(v, str):
                                        tex_name = v.split("'")[1] if "'" in v else v
                                        add_tex(k, tex_name.split('.')[0])
                                        
                            tp_vals = data_item.get("TextureParameterValues", [])
                            if isinstance(tp_vals, list):
                                for tp in tp_vals:
                                    if isinstance(tp, dict):
                                        param_name = tp.get("ParameterInfo", {}).get("Name", "")
                                        param_val = tp.get("ParameterValue")
                                        if param_val:
                                            if isinstance(param_val, dict):
                                                obj_name = param_val.get("ObjectName") or param_val.get("ObjectPath") or ""
                                            else:
                                                obj_name = str(param_val)
                                            if obj_name:
                                                tex_name = obj_name.split("'")[1] if "'" in obj_name else obj_name
                                                add_tex(param_name, tex_name.split('.')[0])
                                                
                            sp_vals = data_item.get("ScalarParameterValues", [])
                            if isinstance(sp_vals, list):
                                for sp in sp_vals:
                                    if isinstance(sp, dict):
                                        param_name = sp.get("ParameterInfo", {}).get("Name", "")
                                        param_val = sp.get("ParameterValue")
                                        if param_name and param_val is not None:
                                            try:
                                                scalars[param_name.lower()] = float(param_val)
                                            except (ValueError, TypeError):
                                                pass
                                                
                            vp_vals = data_item.get("VectorParameterValues", [])
                            if isinstance(vp_vals, list):
                                for vp in vp_vals:
                                    if isinstance(vp, dict):
                                        param_name = vp.get("ParameterInfo", {}).get("Name", "")
                                        param_val = vp.get("ParameterValue")
                                        if param_name and isinstance(param_val, dict):
                                            vectors[param_name.lower()] = (
                                                param_val.get("R", 0.0),
                                                param_val.get("G", 0.0),
                                                param_val.get("B", 0.0),
                                                param_val.get("A", 1.0)
                                            )
                                            
                            for k, v in data_item.items():
                                k_lower = k.lower()
                                if k_lower in ("textureparametervalues", "textures", "scalarparametervalues", "vectorparametervalues"):
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
                                        extract_all_textures(v)
                                elif isinstance(v, str):
                                    if "/textures/" in v.lower() or "texture2d'" in v.lower():
                                        tex_name = v.split("'")[1] if "'" in v else v
                                        add_tex(k, tex_name.split('.')[0])
                                elif isinstance(v, list):
                                    extract_all_textures(v)
                        elif isinstance(data_item, list):
                            for item in data_item:
                                extract_all_textures(item)
                                
                    extract_all_textures(mat_data)
                except Exception as e:
                    print(f"Error parsing material JSON {mat_file}: {e}")
            else:
                try:
                    with open(mat_file, 'r', encoding='utf-8') as f:
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
                    print(f"Error parsing legacy mat file {mat_file}: {e}")
                    
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
                
            # Extract Set A texture parameter mappings
            diffuse_tex = textures.get("diffuse") or textures.get("basecolor") or textures.get("basecolorcomponent") or textures.get("basecolortex") or textures.get("color") or textures.get("albedo") or textures.get("other[0]") or textures.get("pm_diffuse")
            normal_tex = textures.get("normal") or textures.get("normaltex") or textures.get("normalcomponent") or textures.get("pm_normals")
            spec_tex = textures.get("specular") or textures.get("spec") or textures.get("specpower")
            rough_tex = textures.get("roughness") or textures.get("rough") or textures.get("roughnesstex")
            metallic_tex = textures.get("metallic") or textures.get("metal") or textures.get("metallictex")
            mask_tex = textures.get("masks") or textures.get("mask") or textures.get("maskstex") or textures.get("mra") or textures.get("ormh") or textures.get("orm") or textures.get("pm_specularmasks") or textures.get("specularmasks")
            emissive_tex = textures.get("emissive") or textures.get("emissivecolor") or textures.get("emissivetex") or textures.get("emissive_color") or textures.get("emissive_tex") or textures.get("pm_emissive")
            
            # Extract Set B texture parameter mappings (for Vertex-Color blended materials)
            diffuse_tex_b = textures.get("diffuse_b") or textures.get("basecolor_b") or textures.get("basecolor_2") or textures.get("basecolorcomponent_b") or textures.get("basecolortex_b") or textures.get("color_b") or textures.get("albedo_b") or textures.get("other[1]")
            normal_tex_b = textures.get("normal_b") or textures.get("normal_2") or textures.get("normaltex_b") or textures.get("normalcomponent_b")
            rough_tex_b = textures.get("roughness_b") or textures.get("rough_b") or textures.get("roughness_2") or textures.get("roughnesstex_b")
            metallic_tex_b = textures.get("metallic_b") or textures.get("metal_b") or textures.get("metallic_2") or textures.get("metallictex_b")
            mask_tex_b = textures.get("masks_b") or textures.get("mask_b") or textures.get("maskstex_b") or textures.get("mra_b") or textures.get("ormh_b") or textures.get("orm_b") or textures.get("pm_specularmasks_b") or textures.get("specularmasks_b")
            
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
                # No diffuse texture: set solid color if present in vectors
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
                
                for asset_type, ue_path in sorted(referenced_assets, key=lambda x: x[1]):
                    clean_path = clean_unreal_path(ue_path)
                    found = False
                    if asset_type == "mesh" and is_basic_shape(ue_path):
                        found = True
                    elif asset_type == "material" and is_basic_shape_material(ue_path):
                        found = True
                    elif file_index:
                        preferred = {'.gltf', '.glb', '.fbx', '.obj'} if asset_type == "mesh" else {'.json', '.mat'}
                        found_path = resolve_file_path(file_index, clean_path, preferred_exts=preferred)
                        if found_path:
                            found = True
                            
                    if asset_type == "mesh":
                        if found:
                            found_meshes.append(ue_path)
                        else:
                            missing_meshes.append(ue_path)
                    else:
                        if found:
                            found_mats.append(ue_path)
                        else:
                            missing_mats.append(ue_path)
                            
                f.write(f"--- MISSING MESHES ({len(missing_meshes)}) ---\n")
                for path in missing_meshes:
                    f.write(f"{path}\n")
                f.write("\n")
                
                f.write(f"--- MISSING MATERIALS/DECALS ({len(missing_mats)}) ---\n")
                for path in missing_mats:
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

classes = (
    LIS_ReferencedFolderItem,
    LIS_UL_referenced_folders,
    ShowMissingInfoOperator,
    MISettings,
    VIEW3D_PT_map_importer_panel,
    MapImporter,
    MaterialImporter,
    ExportMissingAssetsListOperator,
    RefreshAnalysisOperator,
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

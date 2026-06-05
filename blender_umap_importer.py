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

def update_analysis(self, context):
    try:
        run_asset_analysis(self)
    except Exception as e:
        print(f"Error in update_analysis: {e}")

class LIS_ReferencedFolderItem(bpy.types.PropertyGroup):
    path: bpy.props.StringProperty()
    missing: bpy.props.IntProperty()
    total: bpy.props.IntProperty()

class LIS_UL_referenced_folders(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            icon_name = "FOLDER_REDIRECT" if item.missing > 0 else "FILE_FOLDER"
            if item.missing > 0:
                row.label(text=f"{item.path} ({item.missing}/{item.total} missing)", icon=icon_name)
            else:
                row.label(text=f"{item.path} ({item.total} assets)", icon=icon_name)
        elif self.layout_type == 'GRID':
            pass

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
        self.layout.prop(mytool, "hide_collisions")
        self.layout.prop(mytool, "skip_missing_assets")
        
        row = self.layout.row()
        row.operator(MapImporter.bl_idname, text="Import Level")
        self.layout.label(text="Blender will be unresponsive during import.", icon="ERROR")
        
        self.layout.separator()
        
        # Collapsible Analysis Section
        box = self.layout.box()
        box.prop(mytool, "show_analysis", text="Asset Analysis / Missing Folders", icon="FILE_TEXT", toggle=True)
        if mytool.show_analysis:
            box.label(text=_analysis_results["status"], icon="INFO")
            
            row = box.row()
            row.prop(mytool, "analysis_depth", text="Folder Depth")
            
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

def resolve_file_path(index, ue_path, preferred_exts=None):
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
        
    return None

def resolve_blueprint_mesh(index, template_path):
    """
    Loads and parses a blueprint class template JSON to find the default
    StaticMesh path if it is null or missing in the UMAP level JSON.
    """
    if not template_path:
        return None
        
    parts = template_path.split('.')
    bp_path = parts[0]
    try:
        suffix_idx = int(parts[-1])
    except ValueError:
        suffix_idx = None
        
    bp_json_path = resolve_file_path(index, bp_path, preferred_exts={'.json'})
    if not bp_json_path or not os.path.exists(bp_json_path):
        return None
        
    try:
        with open(bp_json_path, 'r', encoding='utf-8') as f:
            bp_data = json.load(f)
            
        if not isinstance(bp_data, list):
            bp_data = [bp_data]
            
        obj = None
        if suffix_idx is not None and suffix_idx < len(bp_data):
            obj = bp_data[suffix_idx]
        else:
            # Fallback: search for first object with a StaticMesh property
            for item in bp_data:
                if isinstance(item, dict):
                    props = item.get("Properties", {})
                    if "StaticMesh" in props:
                        obj = item
                        break
            
        if obj:
            props = obj.get("Properties", {})
            sm_ref = props.get("StaticMesh")
            if sm_ref and isinstance(sm_ref, dict):
                return sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
    except Exception as e:
        print(f"Error parsing blueprint template {bp_json_path}: {e}")
        
    return None

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
            if etype in ("StaticMeshComponent", "InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent"):
                sm_ref = props.get("StaticMesh")
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
    global _analysis_results
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
            folders_data[folder] = {"missing": 0, "total": 0}
            
        folders_data[folder]["total"] += 1
        
        found = False
        if file_index:
            preferred = {'.gltf', '.glb', '.fbx', '.obj'} if asset_type == "mesh" else {'.json', '.mat'}
            found_path = resolve_file_path(file_index, clean_path, preferred_exts=preferred)
            if found_path:
                found = True
                
        if not found:
            folders_data[folder]["missing"] += 1
            missing_count += 1
            
    folders_list = []
    for f_path, data in folders_data.items():
        folders_list.append({
            "path": f_path,
            "missing": data["missing"],
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
        # Unreal applies ZYX order (Yaw -> Pitch -> Roll) in left-handed coordinates
        euler_ue = mathutils.Euler((math.radians(roll), math.radians(pitch), math.radians(yaw)), 'ZYX')
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
    wound to face +X (projection axis) and offset by 0.5cm to avoid Z-fighting.
    """
    mesh_data = bpy.data.meshes.new(name=f"{name}_Mesh")
    
    # Vertices of the YZ plane facing +X (normal), offset by 0.005m (0.5cm) to prevent Z-fighting
    verts = [
        (0.005, -1.28, -1.28),
        (0.005,  1.28, -1.28),
        (0.005,  1.28,  1.28),
        (0.005, -1.28,  1.28)
    ]
    faces = [(0, 1, 2, 3)]
    
    mesh_data.from_pydata(verts, [], faces)
    mesh_data.update()
    
    # Add UV coordinates so the texture maps correctly
    mesh_data.uv_layers.new(name="UVMap")
    uv_layer = mesh_data.uv_layers.active.data
    uv_layer[0].uv = (0.0, 0.0)
    uv_layer[1].uv = (1.0, 0.0)
    uv_layer[2].uv = (1.0, 1.0)
    uv_layer[3].uv = (0.0, 1.0)
    
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

    def execute(self, context):
        scene = context.scene
        mytool = scene.my_tool
        
        base_dir = mytool.base_directory
        map_json = mytool.json_file
        hide_collisions = mytool.hide_collisions
        skip_missing_assets = mytool.skip_missing_assets
        
        if not os.path.exists(map_json):
            self.report({'ERROR'}, f"Level JSON file not found: {map_json}")
            return {'CANCELLED'}
            
        # 1. Directory indexing
        file_index = index_directory(base_dir)
        
        # 2. Load and parse level JSON
        print(f"Parsing level JSON: {map_json}")
        with open(map_json, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
            
        if not isinstance(json_data, list):
            json_data = [json_data]
            
        total_objects = len(json_data)
        object_by_index = {i: obj for i, obj in enumerate(json_data)}
        
        # Create map collection
        json_filename = os.path.basename(map_json)
        collection_name = os.path.splitext(json_filename)[0]
        import_collection = bpy.data.collections.get(collection_name)
        if not import_collection:
            import_collection = bpy.data.collections.new(collection_name)
            bpy.context.scene.collection.children.link(import_collection)
            
        blender_objs = {}
        local_matrices = {}
        mesh_cache = {}
        parent_relations = {} # child index -> parent index
        
        # Start Blender GUI progress bar
        context.window_manager.progress_begin(0, total_objects)
        
        # Pass 1: Instantiation of all Actors and Components
        print("Pass 1: Creating Blender objects...")
        for i, entity in enumerate(json_data):
            # Update progress indicator
            context.window_manager.progress_update(i)
            
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
                
            # Parse component categories
            if etype in ("StaticMeshComponent", "InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent", "FoliageInstancedStaticMeshComponent"):
                sm_ref = props.get("StaticMesh")
                mesh_path = None
                if sm_ref:
                    mesh_path = sm_ref.get("ObjectPath") or sm_ref.get("ObjectName")
                else:
                    # Try resolving static mesh from template blueprint JSON
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
                    
                    mesh_file = resolve_file_path(file_index, mesh_path, preferred_exts={'.gltf', '.glb', '.fbx', '.obj'})
                    if not mesh_file:
                        print(f"Asset file not found for: {mesh_path}")
                        continue
                        
                    # Cache/import the template mesh
                    base_mesh = None
                    if mesh_path in mesh_cache:
                        base_mesh = mesh_cache[mesh_path]
                    else:
                        base_mesh = import_asset(mesh_file, f"{ename}_Template", import_collection, hide_collisions)
                        if base_mesh:
                            mesh_cache[mesh_path] = base_mesh
                            base_mesh.hide_viewport = True
                            base_mesh.hide_render = True
                            
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
                    mesh_file = resolve_file_path(file_index, mesh_path, preferred_exts={'.gltf', '.glb', '.fbx', '.obj'})
                    if not mesh_file:
                        print(f"Asset file not found for: {mesh_path}")
                        if not skip_missing_assets:
                            empty_obj = bpy.data.objects.new(name=ename, object_data=None)
                            import_collection.objects.link(empty_obj)
                            blender_objs[i] = empty_obj
                        continue
                        
                    mesh_obj = None
                    if mesh_path in mesh_cache:
                        mesh_obj = clone_hierarchy(mesh_cache[mesh_path], ename, import_collection, unhide=True, hide_collisions=hide_collisions)
                    else:
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
                
                # Apply DecalSize if present in properties
                decal_size = props.get("DecalSize", {})
                if decal_size:
                    ds_y = decal_size.get("Y", 128.0) / 128.0
                    ds_z = decal_size.get("Z", 128.0) / 128.0
                    scale_decal_mat = mathutils.Matrix.Diagonal((1.0, ds_y, ds_z, 1.0))
                    local_matrices[i] = local_matrices[i] @ scale_decal_mat
                
            # Default component wrapper (SceneComponent)
            else:
                empty_obj = bpy.data.objects.new(name=ename, object_data=None)
                import_collection.objects.link(empty_obj)
                blender_objs[i] = empty_obj
                
        # Pass 2: Parenting Links
        print("Pass 2: Establishing parenting structures...")
        for i, obj in blender_objs.items():
            if i in parent_relations:
                p_idx = parent_relations[i]
                if p_idx in blender_objs:
                    obj.parent = blender_objs[p_idx]
                    
        # Clean up empty wrappers if skip_missing_assets is enabled
        if skip_missing_assets:
            print("Cleaning up empty placeholders...")
            removed_any = True
            while removed_any:
                removed_any = False
                to_remove = []
                for i, obj in list(blender_objs.items()):
                    if obj.type == 'EMPTY' and not obj.children:
                        to_remove.append((i, obj))
                        
                for i, obj in to_remove:
                    if obj.name in import_collection.objects:
                        import_collection.objects.unlink(obj)
                    bpy.data.objects.remove(obj, do_unlink=True)
                    del blender_objs[i]
                    if i in local_matrices:
                        del local_matrices[i]
                    removed_any = True
                    
        # Pass 3: Apply transforms
        print("Pass 3: Assigning transformations...")
        for i, obj in blender_objs.items():
            if i in local_matrices:
                obj.matrix_basis = local_matrices[i]
                
        # End progress bar
        context.window_manager.progress_end()
        
        print("Level mesh import finished successfully.")
        # Trigger materials rebuild
        bpy.ops.lis.mat_import()
        return {'FINISHED'}

class MaterialImporter(bpy.types.Operator):
    bl_idname = "lis.mat_import"
    bl_label = "Material Importer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        mytool = scene.my_tool
        base_dir = mytool.base_directory
        
        # Index asset directory for materials/textures
        file_index = index_directory(base_dir)
        
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
                        # Replace in all mesh data blocks (mesh-linked materials)
                        for mesh in bpy.data.meshes:
                            for idx, mat in enumerate(mesh.materials):
                                if mat == material:
                                    mesh.materials[idx] = base_mat
                        # Replace in all object material slots (object-linked materials)
                        for obj in bpy.data.objects:
                            for slot in obj.material_slots:
                                if slot.material == material:
                                    slot.material = base_mat
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
                print(f"Material file not found for: {mat_name}")
                continue
                
            textures = {}
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
                                        textures[k.lower()] = tex_name.split('.')[0]
                                        
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
                                                textures[param_name.lower()] = tex_name.split('.')[0]
                                                
                            for k, v in data_item.items():
                                if k.lower() == "properties" and isinstance(v, dict):
                                    extract_all_textures(v)
                                    continue
                                if isinstance(v, str):
                                    if "/textures/" in v.lower() or "texture2d'" in v.lower():
                                        tex_name = v.split("'")[1] if "'" in v else v
                                        textures[k.lower()] = tex_name.split('.')[0]
                                elif isinstance(v, (dict, list)):
                                    if k not in ("TextureParameterValues", "Textures"):
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
                            textures[k.strip().lower()] = v.strip()
                except Exception as e:
                    print(f"Error parsing legacy mat file {mat_file}: {e}")
                    
            if not textures:
                continue
                
            # Extract Set A texture parameter mappings
            diffuse_tex = textures.get("diffuse") or textures.get("basecolor") or textures.get("basecolorcomponent") or textures.get("basecolortex") or textures.get("color") or textures.get("albedo") or textures.get("other[0]") or textures.get("pm_diffuse")
            normal_tex = textures.get("normal") or textures.get("normaltex") or textures.get("normalcomponent") or textures.get("pm_normals")
            spec_tex = textures.get("specular") or textures.get("spec") or textures.get("specpower")
            rough_tex = textures.get("roughness") or textures.get("rough") or textures.get("roughnesstex")
            metallic_tex = textures.get("metallic") or textures.get("metal") or textures.get("metallictex")
            mask_tex = textures.get("masks") or textures.get("mask") or textures.get("maskstex") or textures.get("mra") or textures.get("ormh") or textures.get("orm") or textures.get("pm_specularmasks") or textures.get("specularmasks")
            
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
                    link_socket(diffuse_node_a, "Alpha", "Alpha")
                else:
                    # Single diffuse color setup
                    link_socket(diffuse_node_a, "Base Color")
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
                    if file_index:
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

classes = (
    LIS_ReferencedFolderItem,
    LIS_UL_referenced_folders,
    MISettings,
    VIEW3D_PT_map_importer_panel,
    MapImporter,
    MaterialImporter,
    ExportMissingAssetsListOperator,
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

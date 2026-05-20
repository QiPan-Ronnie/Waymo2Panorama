import importlib
for m in ['open3d', 'pyvista', 'trimesh', 'matplotlib', 'plyfile', 'numpy', 'PIL']:
    try:
        mod = importlib.import_module(m)
        v = getattr(mod, '__version__', '?')
        print(f'{m:12s} {v}  OK')
    except Exception as e:
        print(f'{m:12s} MISSING')

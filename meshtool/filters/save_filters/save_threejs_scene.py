import uuid
from meshtool.filters.base_filters import SaveFilter
import numpy
import collada


def deccolor(c):
    return (int(c[0] * 255) << 16) + (int(c[1] * 255) << 8) + int(c[2] * 255)


# We don't use python json module so that we have more control on formatting (especially float truncation)
def to_json(o):
    ret = ""
    if o is None:
       pass #ret += 'null'
    elif isinstance(o, dict):
        ret += "{"
        comma = ""
        for k,v in o.items():
            ret += comma
            comma = ","
            ret += '"' + str(k) + '":'
            ret += to_json(v)

        ret += "}"
    elif isinstance(o, str):
        ret += '"' + o + '"'
    elif isinstance(o, list):
        ret += "[" + ",".join([to_json(e) for e in o]) + "]"
    elif isinstance(o, bool):
        ret += "true" if o else "false"
    elif isinstance(o, int):
        ret += str(o)
    elif isinstance(o, float):
        ret += '%.7g' % o
    elif isinstance(o, numpy.ndarray) and numpy.issubdtype(o.dtype, numpy.integer):
        ret += "[" + ','.join(map(str, o.flatten().tolist())) + "]"
    elif isinstance(o, numpy.ndarray) and numpy.issubdtype(o.dtype, numpy.inexact):
        ret += "[" + ','.join(['%.3g' % x for x in o.flatten().tolist()]) + "]"
    else:
        raise TypeError("Unknown type '%s' for json serialization" % str(type(o)))
    return ret



class ThreeJSDictGenerator(object):
    def __init__(self, mesh):
        self.mesh = mesh
        self.texture_placements = None

    def to_dict(self):
        outdict = {}
        outdict['metadata'] = {'version': 4.3,
                               'type': 'Object',
                               'generator': 'meshtool'}
        outdict['geometries'] = self.getGeometries()
        outdict['materials'] = self.getMaterials()
        outdict['object'] = self.getScene()
        return outdict

    def save_to(self, filename):
        outputfile = open(filename, 'w')

        outdict = self.to_dict()
        outputfile.write(to_json(outdict))
        outputfile.close()

    def getMaterials(self):
        materials = []
        for material in self.mesh.materials:
            effect = material.effect

            attrs = {}
            attrs['uuid'] = material.id
            attrs['name'] = material.name
            if effect.shadingtype == 'lambert':
                attrs['type'] = 'MeshLambertMaterial'
            elif effect.shadingtype == 'phong' or effect.shadingtype == 'blinn':
                attrs['type'] = 'MeshPhongMaterial'
            else:
                attrs['type'] = 'MeshBasicMaterial'

            color_mapping = [('diffuse', 'color'),
                             ('ambient', 'ambient'),
                             ('specular', 'specular')]
            for effect_attr, three_name in color_mapping:
                val = getattr(effect, effect_attr, None)
                if val is not None and not isinstance(val, collada.material.Map):
                    attrs[three_name] = deccolor(val)

            float_mapping = [('shininess', 'shininess'),
                             ('transparency', 'opacity')]
            for effect_attr, three_name in float_mapping:
                val = getattr(effect, effect_attr, None)
                if val is not None and not isinstance(val, collada.material.Map):
                    attrs[three_name] = val
            attrs['transparent'] = attrs['opacity'] < 1
            map_mapping = [('diffuse', 'map'),
                           ('ambient', 'mapAmbient'),
                           ('specular', 'mapSpecular'),
                           ('bump_map', 'mapNormal')]
            for effect_attr, three_name in map_mapping:
                val = getattr(effect, effect_attr, None)
                if isinstance(val, collada.material.Map):
                    attrs[three_name] = val.sampler.surface.image.id

            # transparency
            trans_color = effect.transparent
            if trans_color is not None:
                transparency = effect.transparency if effect.transparency is not None else 1
                if effect.opaque_mode == 'A_ONE':
                    # Takes the transparency information from the colors alpha channel, where the value 1.0 is opaque.
                    opacity = trans_color[3] * transparency

                elif effect.opaque_mode == 'RGB_ZERO':
                    # Takes the transparency information from the colors red, green, and blue channels,
                    # where the value 0.0 is opaque, with each channel modulated independently.

                    # luminance is the function, based on the ISO/CIE color standards (see ITU-R Recommendation
                    # BT.709-4), that averages the color channels into one value
                    luminance = trans_color[0] * 0.212671 + trans_color[1] * 0.715160 + trans_color[2] * 0.072169
                    opacity = 1.0 - luminance * transparency

                else:
                    raise NotImplementedError

                attrs['opacity'] = opacity
                attrs['transparent'] = opacity < 1



            materials.append(attrs)

        return materials

    def getGeometries(self):
        geometries = []

        for geom in self.mesh.geometries:
            for prim_num, prim in enumerate(geom.primitives):
                if isinstance(prim, collada.polylist.Polylist):
                    prim = prim.triangleset()

                attrs = {}
                attrs['uuid'] = geom.id
                attrs['type'] = 'BufferGeometry'

                data = {}
                data['position'] = {
                    'itemSize': 3,
                    'type': 'Float32Array',
                    'array': prim.vertex.flatten().tolist() if prim.vertex is not None else []
                }
                data['normal'] = {
                    'itemSize': 3,
                    'type': 'Float32Array',
                    'array': prim.normal.flatten().tolist() if prim.normal is not None else []
                }
                data['uv'] = {
                    'itemSize': 2,
                    'type': 'Float32Array',
                    'array': prim.texcoordset[0].flatten().tolist() if len(prim.texcoordset) else []
                }

                attrs['data'] = {
                    'attributes': data,
                    'index': {
                        'type': 'Uint32Array',
                        'array': prim.vertex_index.flatten().tolist() if prim.vertex_index.any is not None else []
                    }
                }

                geometries.append(attrs)

        return geometries

    def getScene(self):
        children = []
        scale = self.mesh.assetInfo.unitmeter;
        object = {
            'uuid': str(uuid.uuid4()),
            'type': 'Scene',
            'matrix': [scale, 0, 0, 0,
                       0, scale, 0, 0,
                       0, 0, scale, 0,
                       0, 0, 0, 1],
            'children': children
        }

        matrix = numpy.identity(4)
        print(self.mesh.assetInfo.upaxis)
        #if self.mesh.assetInfo.upaxis == collada.asset.UP_AXIS.X_UP:
        #    r = collada.scene.RotateTransform(0, 1, 0, -90)
        #    matrix = r.matrix
        #elif self.mesh.assetInfo.upaxis == collada.asset.UP_AXIS.Z_UP:
        #    r = collada.scene.RotateTransform(1, 0, 0, -90)
        #    matrix = r.matrix

        if self.mesh.scene is not None:
            for boundgeom in self.mesh.scene.objects('geometry'):
                for prim_num, boundprim in enumerate(boundgeom.primitives()):
                    attrs = {}
                    attrs['uuid'] = str(uuid.uuid4())
                    attrs['name'] = boundgeom.original.name
                    attrs['material'] = boundprim.material.id
                    attrs['matrix'] = boundgeom.matrix.flatten('F').tolist()
                    attrs['type'] = 'Mesh'
                    attrs['geometry'] = boundgeom.original.id
                    children.append(attrs)

        return object


def FilterGenerator():
    class ThreeJsSceneSaveFilter(SaveFilter):
        def __init__(self):
            super(ThreeJsSceneSaveFilter, self).__init__('save_threejs_scene',
                                                         'Saves a collada model in three.js 4.3 scene format')

        def apply(self, mesh, filename):
            # if os.path.exists(filename):
            #    raise FilterException('specified filename already exists')

            generator = ThreeJSDictGenerator(mesh)
            generator.save_to(filename)

            return mesh

    return ThreeJsSceneSaveFilter()


from meshtool.filters import factory

factory.register(FilterGenerator().name, FilterGenerator)

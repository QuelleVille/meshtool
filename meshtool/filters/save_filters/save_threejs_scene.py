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
        outdict['materials'] = self.getMaterials()
        outdict['object'], outdict['geometries'] = self.getScene()
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
            attrs['name'] = material.name or ''
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


    def serializeBoundPrimitives(self, primitives, geom_id):

        offset = 0
        indices_array = None
        positions_array = None
        normals_array = None
        for prim in primitives:
            if isinstance(prim, collada.polygons.BoundPolygons):
                prim = prim.triangleset()

            if positions_array is None:
                positions_array = prim.vertex
            else:
                positions_array = numpy.concatenate((positions_array, prim.vertex))



            if normals_array is None:
                normals_array = prim.normal
            else:
                normals_array = numpy.concatenate((normals_array, prim.normal))

            if indices_array is None:
                indices_array = prim.vertex_index
            else:
                indices_array = numpy.concatenate((indices_array, prim.vertex_index + offset))

            offset += int(len(positions_array) / 3)

        return {
            'uuid': geom_id,
            'type': 'BufferGeometry',
            'data': {
                'index': {
                    'type': 'Uint32Array',
                    'array': indices_array
                },
                'attributes': {
                    'position':{
                        'itemSize': 3,
                        'type': 'Float32Array',
                        'array': positions_array
                    },
                    'normal':{
                        'itemSize': 3,
                        'type': 'Float32Array',
                        'array': normals_array if normals_array is not None else []
                    },
                }
            },
        }


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


        primitives_by_mat = {}
        if self.mesh.scene is not None:
            for boundgeom in self.mesh.scene.objects('geometry'):
                for boundprim in boundgeom.primitives():
                    mat = boundprim.material.id
                    primitives = primitives_by_mat.setdefault(mat, [])
                    primitives.append((boundgeom, boundprim))

        geometries = []

        for mat, prims in primitives_by_mat.items():
            for geom, prim in prims:

                geom_id = str(uuid.uuid4()) #geom.original.id

                children.append({
                    'uuid':  str(uuid.uuid4()),
                    'material': mat,
                    'type': 'Mesh',
                    'geometry': geom_id
                })

                geometries.append(self.serializeBoundPrimitives([prim], geom_id))

        return object, geometries


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

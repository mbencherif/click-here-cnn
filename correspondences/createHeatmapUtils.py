import scipy.io as spio
import numpy as np
import caffe
import lmdb
from warnings import warn

import os.path

from pprint import pprint

######### Universal constants ################################################

RENDER4CNN_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PASCAL3D_ROOT = os.path.join(RENDER4CNN_ROOT, 'datasets', 'pascal3d')
ANNOTATIONS_ROOT = os.path.join(PASCAL3D_ROOT, 'Annotations')
IMAGES_ROOT = os.path.join(PASCAL3D_ROOT, 'Images')
HEATMAP_ROOT = 'heatmaps'
SCALED_IMAGES_ROOT = 'scaled'
DATASET_SOURCES = ['pascal', 'imagenet']
IMAGE_SIZE = 227

######### IDs for keypoints ##################################################

KEYPOINT_TYPES = {
    'aeroplane': ['left_elevator', 'left_wing', 'noselanding', 'right_elevator', 'right_wing', 'rudder_lower', 'rudder_upper', 'tail'], 
    'bicycle': ['head_center', 'left_back_wheel', 'left_front_wheel', 'left_handle', 'left_pedal_center', 'right_back_wheel', 'right_front_wheel', 'right_handle', 'right_pedal_center', 'seat_back', 'seat_front'], 
    'boat': ['head', 'head_down', 'head_left', 'head_right', 'tail_left', 'tail_right', 'tail'], 
    'bottle': ['mouth', 'body', 'body_left', 'body_right', 'bottom', 'bottom_left', 'bottom_right'], 
    'bus': ['body_back_left_lower', 'body_back_left_upper', 'body_back_right_lower', 'body_back_right_upper', 'body_front_left_upper', 'body_front_right_upper', 'body_front_left_lower', 'body_front_right_lower', 'left_back_wheel', 'left_front_wheel', 'right_back_wheel', 'right_front_wheel'], 
    'car': ['left_front_wheel', 'left_back_wheel', 'right_front_wheel', 'right_back_wheel', 'upper_left_windshield', 'upper_right_windshield', 'upper_left_rearwindow', 'upper_right_rearwindow', 'left_front_light', 'right_front_light', 'left_back_trunk', 'right_back_trunk'], 
    'chair': ['back_upper_left', 'back_upper_right', 'seat_upper_left', 'seat_upper_right', 'seat_lower_left', 'seat_lower_right', 'leg_upper_left', 'leg_upper_right', 'leg_lower_left', 'leg_lower_right'], 
    'diningtable': ['leg_upper_left', 'leg_upper_right', 'leg_lower_left', 'leg_lower_right', 'top_upper_left', 'top_upper_right', 'top_lower_left', 'top_lower_right', 'top_up', 'top_down', 'top_left', 'top_right'], 
    'motorbike': ['back_seat', 'front_seat', 'head_center', 'headlight_center', 'left_back_wheel', 'left_front_wheel', 'left_handle_center', 'right_back_wheel', 'right_front_wheel', 'right_handle_center'], 
    'sofa': ['front_bottom_left', 'front_bottom_right', 'seat_bottom_left', 'seat_bottom_right', 'left_bottom_back', 'right_bottom_back', 'top_left_corner', 'top_right_corner', 'seat_top_left', 'seat_top_right'], 
    'train': ['head_left_bottom', 'head_left_top', 'head_right_bottom', 'head_right_top', 'head_top', 'mid1_left_bottom', 'mid1_left_top', 'mid1_right_bottom', 'mid1_right_top', 'mid2_left_bottom', 'mid2_left_top', 'mid2_right_bottom', 'mid2_right_top', 'tail_left_bottom', 'tail_left_top', 'tail_right_bottom', 'tail_right_top'], 
    'tvmonitor': ['front_bottom_left', 'front_bottom_right', 'front_top_left', 'front_top_right', 'back_bottom_left', 'back_bottom_right', 'back_top_left', 'back_top_right']
}

_keyptLabelToIdMap = {}
_keyptIdToLabelMap = {}

#_keyptTypeIds = {}
_rigidClasses = []
numKeyptTypes = 124

def keyptClassType2Label(rigidClass, keyptType):
    assert(rigidClass in KEYPOINT_TYPES.keys())
    assert(keyptType in KEYPOINT_TYPES[rigidClass])
    return '%s_%s' % (rigidClass, keyptType)

def _buildKeyptTypeIdMaps():
    count = 0
    for rigidClass in sorted(KEYPOINT_TYPES.keys()):
        for keyptType in sorted(KEYPOINT_TYPES[rigidClass]):
            label = keyptClassType2Label(rigidClass, keyptType)
            _keyptLabelToIdMap[label] = count
            _keyptIdToLabelMap[count] = label
            count += 1
_buildKeyptTypeIdMaps()

def getKeyptTypeId(rigidClass, keyptType):
    return _keyptLabelToIdMap[keyptClassType2Label(rigidClass, keyptType)]

def getKeyptTypeLabel(keyptTypeId):
    return _keyptIdToLabelMap[keyptTypeId]

def getRigidClasses():
    return sorted(KEYPOINT_TYPES.keys())

######### Importing .mat files ###############################################
######### Reference: http://stackoverflow.com/a/8832212 ######################

def loadmat(filename):
    '''
    this function should be called instead of direct spio.loadmat
    as it cures the problem of not properly recovering python dictionaries
    from mat files. It calls the function check keys to cure all entries
    which are still mat-objects
    '''
    data = spio.loadmat(filename, struct_as_record=False, squeeze_me=True)
    return _check_keys(data)

def _check_keys(dict):
    '''
    checks if entries in dictionary are mat-objects. If yes
    todict is called to change them to nested dictionaries
    '''
    for key in dict:
        if isinstance(dict[key], spio.matlab.mio5_params.mat_struct):
            dict[key] = _todict(dict[key])
    return dict        

def _todict(matobj):
    '''
    A recursive function which constructs from matobjects nested dictionaries
    '''
    dict = {}
    for strg in matobj._fieldnames:
        elem = matobj.__dict__[strg]
        if isinstance(elem, spio.matlab.mio5_params.mat_struct):
            dict[strg] = _todict(elem)
        # Handle case where elem is an array of mat_structs
        elif isinstance(elem, np.ndarray) and len(elem) > 0 and \
                isinstance(elem[0], spio.matlab.mio5_params.mat_struct):
            dict[strg] = np.array([_todict(subelem) for subelem in elem])
        else:
            dict[strg] = elem
    return dict

######### Saving to LMDB #####################################################

def saveImgToLMDB(txn, key, img):
    # Convert image to weird Caffe format. Fork depending on if image is color or not.
    if img.ndim == 2:
        # If grayscale, transpose to match Caffe dim ordering
        imgCaffe = img.reshape((1, img.shape[0], img.shape[1]))
    else:
        # If color, switch from RGB to BGR
        imgCaffe = img[:,:,::-1]
        # Go from H*W*C to C*H*W
        imgCaffe = imgCaffe.transpose(2, 0, 1)
    # Populate datum
    datum = caffe.proto.caffe_pb2.Datum()
    (datum.channels, datum.height, datum.width) = imgCaffe.shape
    datum.data = imgCaffe.tobytes()
    # Put datum into LMDB
    txn.put(key.encode('ascii'), datum.SerializeToString())

def saveVecToLMDB(txn, key, vec):
    # Reshape vector to be in Caffe format
    vecCaffe = vec.reshape([len(vec), 1, 1])
    # Put datum into LMDB
    datum = caffe.io.array_to_datum(vecCaffe)
    txn.put(key.encode('ascii'), datum.SerializeToString())

######### Loading from LMDB (for debugging) ##################################

def lmdbStrToImage(lmdbStr):
    # Get datum from LMDB
    datum = caffe.proto.caffe_pb2.Datum()
    datum.ParseFromString(lmdbStr)
    # Retrieve image in weird Caffe format
    img = caffe.io.datum_to_array(datum)
    # Change C*H*W to H*W*C
    img = img.transpose((1, 2, 0))
    # Squeeze extra dimension if grayscale
    img = img.squeeze()
    # Change BGR to RGB if image is in color
    if img.ndim == 3:
        img = img[:,:,::-1]
    return img

# Adapted from Render For CNN's caffe_utils function load_vector_from_lmdb
def lmdbStrToVec(lmdbStr):
    # Get datum from LMDB
    datum = caffe.proto.caffe_pb2.Datum()
    datum.ParseFromString(lmdbStr)
    # Parse to array
    array =  caffe.io.datum_to_array(datum)
    array = np.squeeze(array)
    assert(array is not None)
    return array

# Return a dict of the first N items in the given LMDB (or all if <N items exist)
# X is a function that takes an LMDB string and returns the desired item
def _getFirstNLmdbX(lmdbPath, N, X):
    imgLMDB = lmdb.open(lmdbPath, readonly=True)
    with imgLMDB.begin() as txn:
        # Warn if N is greater than number of pairs in LMDB
        maxN = txn.stat()['entries']    
        if N > maxN:
            warn('Only %d values in LMDB, showing all' % maxN)
    
        # Go through LMDB and populate array with images
        cursor = txn.cursor()
        ret = {}
        for i in range(min(N, maxN)):
            cursor.next()
            key = cursor.key()
            item = X(cursor.value())
            ret[key] = item
        return ret


def getFirstNLmdbImgs(lmdbPath, N):
    return _getFirstNLmdbX(lmdbPath, N, lmdbStrToImage)

def getFirstNLmdbVecs(lmdbPath, N):
    return _getFirstNLmdbX(lmdbPath, N, lmdbStrToVec)

############ Other stuff

def insideBox(point, box):
    return point[0] >= box[0] and point[0] < box[2] \
            and point[1] >= box[1] and point[1] < box[3]
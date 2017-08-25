"""
Overview
--------

This module contains a routines to convert the output of

    sklearn.datasets.make_blobs
    sklearn.datasets.make_regression
    sklearn.datasets.make_classification

to the following containers

    xarray.Dataset
    numpy.array
    pandas.DataFrame

as well as allowing us to do some postprocessing of the data conveniently.

The sklearn functions all output a tuple (X, y) where

    X is a n_samples by n_features numpy.array of features
    y is a n_samples numpy.array of labels

While the simulations/sampling done by sklearn is useful, the final output
may fall short of users with more complex needs.

Why this is useful
------------------

The reasons to wrap the sklearn routines are:

- We want new types easily (xarray.Dataset, etc.)
- We want multidimensional features.
- We want to generate sample weights together with synthetic data.

Motivating usecases:

- Generate data to test models that predict temperature or humidity at
  3d-spatial coordinates and time based on earlier measurements at nearby/same
  coordinates.

Approach
--------

The idea here is to redefine in this module the sklearn functions mentioned
above. Each one of the new functions has the same signature as in sklearn, and
does the same computation, but returns a XyTransformer object that has various
methods to postprocess that (X, y) data. For example, you can do

>>> m = make_blobs()
>>> m.to_dataframe(feature_names=['feat1', 'feat2'], label_name='response')

to convert the data generated by sklearn to pandas data structures suitably
named.

"""



import inspect
import string

import numpy as np
import xarray as xr
import pandas as pd
import sklearn.datasets

from collections import Sequence, OrderedDict, defaultdict
from functools import partial, wraps

def synthetic_coords(shape, layers, dims):
    coord_np_arrs = tuple(np.arange(s) for s in shape)
    space = pd.MultiIndex.from_arrays(coord_np_arrs, names=dims)
    # TODO use PR 3's constants.py default dimension
    # names rather than hard coding them here
    coords = OrderedDict([('space', space), ('layer', layers)])
    dims = ('space', 'layer')
    return coords, dims


OK_METHOD_KW = ('shape', 'dims', 'feature_names',) # FILL THIS OUT ? TODO - Gui?

class XyTransformer:
    "Transforms a pair (feature_matrix, labels_vector) with to_* methods."
    def __init__(self, X, y=None):
        """Initalizes an XyTransformer object.

        Access the underlying feature matrix X and labels y with self.X and
        self.y, respectively.
        """
        self.X = X  # always a 2d numpy.array
        self.y = y  # always a 1d numpy.array

    def to_array(self, feature_shape=None):
        "Return X, y NumPy arrays with given shape"
        if feature_shape:
            X, y = self.X.reshape(feature_shape), self.y
        else:
            X, y = self.X, self.y
        return X, y

    def to_dataframe(self, feature_names=None, label_name=None):
        "Return a dataframe X and a label series y with named variables."
        Xdf = pd.DataFrame(self.X, columns=feature_names)
        ys = pd.Series(self.y, name=label_name)
        return Xdf, ys

    def to_dataset(self, layers=None, shape=None, dims=None):
        """Return an Xarray with layers and space

        [TODO]: improve this docstring.

        [TODO]: should this be a static method that takes data as input?
        """
        assert self.X.ndim == 2
        ncols = self.X.shape[1]
        if shape is None:
            shape = (self.y.size, 1)
        if dims is None:
            defaults = ('x', 'y', 'z', 't')
            if len(shape) <= len(defaults):
                dims = defaults[:len(shape)]
            else:
                dims = defaults + tuple(s for s in string.ascii_letters if s not in defaults)
                dims = dims[:len(shape)]
                assert len(dims) == len(shape)
        if not layers:
            layers = ['label_{}'.format(c) for c in range(ncols)]
        coords, dims = synthetic_coords(self.X.shape, layers, dims)
        xarr = xr.DataArray(self.X, coords=coords, dims=dims)
        # TODO xr.Dataset -> MLDataset from PR 3
        dset = xr.Dataset(OrderedDict([('features', xarr)]))
        return dset, self.y

    def to_ml_features(self, layers=None, shape=None, dims=None, **features_kw):
        dset, _ = self.to_dataset(layers=layers, shape=shape, dims=dims)
        # TODO - This won't work until PR 3 merge and change
        # is made in above TODO note on MLDataset
        dset = dset.to_ml_features(**features_kw)
        return dset, self.y


def _make_base(skl_sampler_func):
    """Maps a make_* function from sklearn to a XyTransformer

    The goal is to use the make_* functions from sklearn to generate the data,
    but then postprocess it with the various to_* methods from a XyTransformer
    class.

    Note: a decorator doesn't solve this. It could add the functionality, but
    it would not change the docstring or the signature of the function, as it
    would keep the original one from the wrapped function from sklearn. So we
    have to do the process manually.

    TODO - Gui - Ensure the default data structures returned are:
      * X -> MLDataset from PR 3 (xr.Dataset for now)
      * y -> Should be a numpy 1-D array
        * TODO - Gui - after this PR is merged, make an issue
          in xarray_filters to support different data structures
          for y - eventually y may be a DataArray, Series,
          * See scikit-learn docs - warnings - do we want a 1-D array for
          y into scikit-learn or do we want a 2-D array for y that
          has only 1 column.  (.squeeze?)
            * Try to summarize plan for what shape y should be
              for most methods - we'll need to standardize y
              in the final step of any chained transformers
    """
    def wrapper(*args, **kwargs):
        '''
        TODO - Gui see if we can do something
               like this instead so that we don't
               return an XyTransformer but
               rather the output of an
               XyTransformer method

        sk_args, sk_kw = ...do something to get only
                          args/kwargs that sklearn.dataset
                          func needs
        other_args, other_kw = ...do something to get only
                               args/kwargs related to
                               XyTransformer
        Check sklearn docs, but I think that
            there are no args - just kwargs
        X, y = skl_sampler_func(*sk_args, **sk_kw)
        xyt = XyTransformer(X, y)
        method = .....some way of mapping to the correct
                      XyTransformer method, like getting
                      the method out of other_kw - related to "astype"
        func = getattr(xyt, method)
        return func(*other_args, **other_kw)

        Some of other_kw -> may be calculated from sk_kw or vice versa?, e.g.
            sk_kw - n_samples
            other_kw - shape (must be consistent)
            Take shape keyword or Exception if n_samples given
        '''
        X, y = skl_sampler_func(*args, **kwargs)
        return XyTransformer(X, y)
    # We now have some tasks: (1) fix the docstring and (2) fix the signature of `wrapper`.
    # Task 1 of 2: fixing the docstring of `wrapper`
    preamble_doc = """Like {skl_funcname}, but returns an XyTransformer object.

    See Also
    --------
    {skl_funcname}
    {xy_transformer}

    """.format(
            skl_funcname=(skl_sampler_func.__module__ + '.' + skl_sampler_func.__name__),
            xy_transformer=(XyTransformer.__module__ + '.' + XyTransformer.__name__)
    )
    wrapper.__doc__ = preamble_doc # + skl_sampler_func.__doc__
    # Task 2 of 2: fixing the signature of `wrapper`
    argspec = inspect.getfullargspec(skl_sampler_func)
    params = [inspect.Parameter(name=pname, kind=inspect.Parameter.POSITIONAL_OR_KEYWORD, default=pdefault)
              for (pname, pdefault) in zip(argspec.args, argspec.defaults)]
    wrapper.__signature__ = inspect.Signature(params)
    wrapper.__name__ = skl_sampler_func.__name__
    return wrapper


def fetch_lfw_people(*args, **kw):
    '''TODO Gui wrap docs from sklearn equivalent
    and add notes about it returning MLDataset

    out = fetch_lfw_people()
    In [6]: out.ariel_sharon_65
Out[6]:
<xarray.DataArray 'ariel_sharon_65' (row: 62, column: 47)>
array([[ 111.666664,  128.333328,  123.333336, ...,  186.333328,  188.      ,
         188.333328],
       [ 103.      ,  124.666664,  121.      , ...,  184.333328,  186.      ,
         187.333328],
       [  97.      ,  119.333336,  114.666664, ...,  178.333328,  180.333328,
         183.333328],
       ...,
       [  23.666666,   24.      ,   20.      , ...,  182.      ,  192.666672,
         200.      ],
       [  21.333334,   20.666666,   18.      , ...,  191.      ,  201.      ,
         202.666672],
       [  20.666666,   18.      ,   14.666667, ...,  197.333328,  202.      ,
         199.333328]], dtype=float32)
Coordinates:
  * row      (row) int64 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 ...
  * column   (column) int64 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 ...
Attributes:
    id:       373
    name:     Ariel Sharon
    '''
    out = sklearn.datasets.fetch_lfw_people(*args, **kw)
    dset = OrderedDict()
    name_tracker = defaultdict(lambda: -1)
    for img, name_id in zip(out.images, out.target):
        name = out.target_names[name_id]
        name_tracker[name] += 1
        clean_name = name.lower().replace(' ', '_') + '_' + str(name_tracker[name])
        r, c = img.shape
        coords = OrderedDict([('row', np.arange(r)),
                              ('column', np.arange(c))])
        dims = ('row', 'column',)
        attrs = dict(name=name, id=name_id)
        data_arr = xr.DataArray(img, coords=coords, dims=dims, attrs=attrs)
        dset[clean_name] = data_arr
    attrs = dict(metadata=[args, kw])
    # TODO - PR 3's MLDataset instead of Dataset
    dset = xr.Dataset(dset, attrs=attrs)
    # TODO - allow converting to dataframe, numpy array?
    return dset


sklearn_make_funcs = [_ for _ in dir(sklearn.datasets) if _.startswith('make_')]
for func_name in sklearn_make_funcs:
    func = getattr(sklearn.datasets, func_name)
    globals()[func_name] = _make_base(func)


extras = ['XyTransformer', 'fetch_lfw_people']
__all__ = sklearn_make_funcs + extras

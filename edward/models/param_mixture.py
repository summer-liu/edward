from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import six
import tensorflow as tf

from edward.models.random_variable import RandomVariable
from tensorflow.contrib.distributions import Distribution

try:
  from edward.models.random_variables import Categorical
except Exception as e:
  raise ImportError("{0}. Your TensorFlow version is not supported.".format(e))


class ParamMixture(RandomVariable, Distribution):
  """A mixture distribution where all components are of the same family.

  Note that this distribution actually represents the conditional
  distribution of the observable variable given a latent categorical
  variable `cat` saying which mixture component generated this
  distribution."""
  def __init__(self,
               mixing_weights,
               component_params,
               component_dist,
               validate_args=False,
               allow_nan_stats=True,
               name="ParamMixture",
               *args, **kwargs):
    """Initialize a batch of mixture random variables.

    Parameters
    ----------
    mixing_weights : tf.Tensor
      (Normalized) weights whose inner (right-most) dimension matches
      the number of components.
    component_params : dict
      Parameters of the per-component distributions.
    component_dist : RandomVariable
      Distribution of each component. The outer (left-most) dimension
      of its batch shape when instantiated determines the number of
      components.

    Notes
    -----
    Given ``ParamMixture``'s ``sample_shape``, ``batch_shape``, and
    ``event_shape``, its ``components`` has shape
    ``sample_shape + [num_components] + batch_shape + event_shape``,
    and its ``cat`` has shape ``sample_shape + batch_shape``.

    Examples
    --------
    >>> probs = tf.ones(5) / 5.0
    >>> params = {'mu': tf.zeros(5), 'sigma': tf.ones(5)}
    >>> x = ParamMixture(probs, params, Normal)
    >>> assert x.shape == ()
    >>>
    >>> probs = tf.ones([2, 5]) / 5.0
    >>> params = {'p': tf.zeros([5, 2]) + 0.8}
    >>> x = ParamMixture(probs, params, Bernoulli)
    >>> assert x.shape == (2,)
    """
    parameters = locals()
    parameters.pop("self")
    values = [mixing_weights] + list(six.itervalues(component_params))
    with tf.name_scope(name, values=values) as ns:
      if validate_args:
        if not isinstance(component_params, dict):
          raise TypeError("component_params must be a dict.")
        elif not isinstance(component_dist, RandomVariable):
          raise TypeError("component_dist must be a ed.RandomVariable object.")

      sample_shape = kwargs.get('sample_shape', ())
      self._mixing_weights = tf.identity(mixing_weights, name="mixing_weights")
      self._cat = Categorical(p=self._mixing_weights,
                              validate_args=validate_args,
                              allow_nan_stats=allow_nan_stats,
                              sample_shape=sample_shape)
      self._component_params = component_params
      self._components = component_dist(validate_args=validate_args,
                                        allow_nan_stats=allow_nan_stats,
                                        sample_shape=sample_shape,
                                        **component_params)

      if validate_args:
        if not self._mixing_weights.shape[-1].is_compatible_with(
                self._components.get_batch_shape()[0]):
          raise TypeError("Last dimension of mixing_weights must match with "
                          "the first dimension of components.")
        elif not self._mixing_weights.shape[:-1].is_compatible_with(
                self._components.get_batch_shape()[1:]):
          raise TypeError("Dimensions of mixing_weights are not compatible "
                          "with the dimensions of components.")

      try:
        self._num_components = self._cat.p.shape.as_list()[-1]
      except:  # if p has TensorShape None
        raise NotImplementedError("Number of components must be statically "
                                  "determined.")

      self._mean_val = None
      self._variance_val = None
      self._stddev_val = None
      if self._cat.p.shape.ndims <= 1:
        with tf.name_scope('means'):
          try:
            comp_means = self._components.mean()
            comp_vars = self._components.variance()
            comp_mean_sq = tf.square(comp_means) + comp_vars

            # weights has shape batch_shape + [num_components]; change
            # to broadcast with [num_components] + batch_shape + event_shape.
            # The below reshaping only works for empty batch_shape.
            weights = self._cat.p
            event_rank = self._components.get_event_shape().ndims
            for _ in range(event_rank):
              weights = tf.expand_dims(weights, -1)

            self._mean_val = tf.reduce_sum(comp_means * weights, 0,
                                           name='mean')
            mean_sq_val = tf.reduce_sum(comp_mean_sq * weights, 0,
                                        name='mean_squared')
            self._variance_val = tf.subtract(mean_sq_val,
                                             tf.square(self._mean_val),
                                             name='variance')
            self._stddev_val = tf.sqrt(self._variance_val, name='stddev')
          except:
            # This fails if _components.{mean,variance}() fails.
            pass

      super(ParamMixture, self).__init__(
          dtype=self._components.dtype,
          is_continuous=self._components.is_continuous,
          is_reparameterized=False,
          validate_args=validate_args,
          allow_nan_stats=allow_nan_stats,
          parameters=parameters,
          name=ns,
          *args, **kwargs)

  @property
  def cat(self):
    return self._cat

  @property
  def components(self):
    return self._components

  @property
  def num_components(self):
    return self._num_components

  def _batch_shape(self):
    return self.cat.batch_shape()

  def _get_batch_shape(self):
    return self.cat.get_batch_shape()

  def _event_shape(self):
    return self.components.event_shape()

  def _get_event_shape(self):
    return self.components.get_event_shape()

#   # This will work in TF 1.1
#   @distribution_util.AppendDocstring(
#     'Note that this function returns the conditional log probability of the '
#     'observed variable given the categorical variable `cat`. For the '
#     'marginal log probability, use `marginal_log_prob()`.')
  def _log_prob(self, x, conjugate=False, **kwargs):
    batch_event_rank = (self.get_event_shape().ndims +
                        self.get_batch_shape().ndims)
    expanded_x = tf.expand_dims(x, -1 - batch_event_rank)
    if conjugate:
      log_probs = self.components.conjugate_log_prob(expanded_x)
    else:
      log_probs = self.components.log_prob(expanded_x)

    selecter = tf.one_hot(self.cat, self.num_components, dtype=tf.float32,
                          axis=self.components.shape.ndims -
                          1 - batch_event_rank)
    return tf.reduce_sum(log_probs * selecter, -1 - batch_event_rank)

  def conjugate_log_prob(self):
    return self._log_prob(self, conjugate=True)

  def marginal_log_prob(self, x, **kwargs):
    'The marginal log probability of the observed variable. Sums out `cat`.'
    batch_event_rank = (self.get_event_shape().ndims +
                        self.get_batch_shape().ndims)
    expanded_x = tf.expand_dims(x, -1 - batch_event_rank)
    log_probs = self.components.log_prob(expanded_x)

    p_ndims = self.cat.p.shape.ndims
    perm = tf.concat([[p_ndims - 1], tf.range(p_ndims - 1)], 0)
    transposed_p = tf.transpose(self.cat.p, perm)

    return tf.reduce_logsumexp(log_probs + tf.log(transposed_p),
                               -1 - batch_event_rank)

  def _sample_n(self, n, seed=None):
    if getattr(self, '_value', None) is not None:
      cat_sample = self.cat.sample(n)
      comp_sample = self.components.sample(n)
    else:
      cat_sample = self.cat
      comp_sample = self.components
      # Add a leading dimension like Distribution.sample(1) would.
      if n == 1:
        comp_sample = tf.expand_dims(comp_sample, 0)
        cat_sample = tf.expand_dims(cat_sample, 0)

    # TODO avoid sampling n per component
    batch_event_rank = (self.get_event_shape().ndims +
                        self.get_batch_shape().ndims)
    cat_axis = comp_sample.shape.ndims - 1 - batch_event_rank
    selecter = tf.one_hot(cat_sample, self.num_components,
                          axis=cat_axis, dtype=self.dtype)

    # selecter has shape [n] + [num_components] + batch_shape; change
    # to broadcast with [n] + [num_components] + batch_shape + event_shape.
    while selecter.shape.ndims < comp_sample.shape.ndims:
      selecter = tf.expand_dims(selecter, -1)

    # select the sampled component, sum out the component dimension
    result = tf.reduce_sum(comp_sample * selecter, cat_axis)
    return result

  def _mean(self):
    if self._mean_val is None:
      raise NotImplementedError()

    return self._mean_val

  def _std(self):
    if self._stddev_val is None:
      raise NotImplementedError()

    return self._stddev_val

  def _variance(self):
    if self._variance_val is None:
      raise NotImplementedError()

    return self._variance_val

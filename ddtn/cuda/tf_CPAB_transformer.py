    #!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Nov 20 09:30:10 2017

@author: nsde
"""

#%% Packages
import tensorflow as tf
from tensorflow.python.framework import function
from tensorflow.python.framework import ops
from ddtn.helper.utility import load_basis, get_dir
from ddtn.helper.tf_funcs import tf_repeat_matrix, tf_expm3x3_analytic

#%% Load dynamic module
def load_dynamic_modules():
    dir_path = get_dir(__file__)
    print('Loading file: ', dir_path + '/./CPAB_ops.so')
    transformer_module = tf.load_op_library(dir_path + '/./CPAB_ops.so')
    transformer_op = transformer_module.calc_trans
    grad_op = transformer_module.calc_grad
    
    return transformer_op, grad_op

transformer_op, grad_op = load_dynamic_modules()
    
#%%
def _calc_trans(points, theta):
    """ Tensorflow wrapper function for calculating the CPAB transformations.
        The function extracts information for the current tesselation basis, and
        then call the dynamic library functions compiled from the cpp code which
        do the actual computations
        
    Arguments:
        points: `Matrix` [2, nb_points]. Grid of 2D points to transform
        theta: `Matrix` [n_theta, dim]. Batch of parametrization vectors. Each
            row specifies a specific transformation
        
    Output:
        newpoints: 3D-`Tensor` [n_theta, 2, nb_points]. Tensor of transformed points.
            The slice newpoints[i] corresponds to the input points transformed
            using the parametrization vector theta[i].
        o
    """
    with tf.variable_scope('calc_trans'):
        # Make sure that both inputs are in float32 format
        points = tf.cast(points, tf.float32) # format [2, nb_points]
        theta = tf.cast(theta, tf.float32) # format [n_theta, dim]
        n_theta = tf.shape(theta)[0]
        
        # Load file with basis
        file = load_basis()
        
        # Tessalation information
        nC = tf.cast(file['nC'], tf.int32)
        ncx = tf.cast(file['ncx'], tf.int32)
        ncy = tf.cast(file['ncy'], tf.int32)
        inc_x = tf.cast(file['inc_x'], tf.float32)
        inc_y = tf.cast(file['inc_y'], tf.float32)
        
        # Steps sizes
        # NOTE: If this number is changed, then the allocation of the cell index
        # need to be changed in the CPAB_ops.cc file as well
        nStepSolver = tf.cast(50, dtype = tf.int32) 
        dT = 1.0 / tf.cast(nStepSolver , tf.float32)
        
        # Get cpab basis
        B = tf.cast(file['B'], tf.float32)

        # Repeat basis for batch multiplication
        B = tf_repeat_matrix(B, n_theta)

        # Calculate the row-flatted affine transformations Avees 
        Avees = tf.matmul(B, tf.expand_dims(theta, 2))
		
        # Reshape into (number of cells, 2, 3) tensor
        As = tf.reshape(Avees, shape = (n_theta * nC, 2, 3)) # format [n_theta * nC, 2, 3]
        
        # Multiply by the step size and do matrix exponential on each matrix
        Trels = tf_expm3x3_analytic(dT*As)
        Trels = tf.reshape(Trels, shape=(n_theta, nC, 2, 3))

        # Call the dynamic library
        with tf.variable_scope('calc_trans_op'):
	        newpoints = transformer_op(points, Trels, nStepSolver, ncx, ncy, inc_x, inc_y)
        return newpoints

#%%
def _calc_grad(op, grad): #grad: n_theta x 2 x nP
    """ Tensorflow wrapper function for calculating the gradient of the CPAB 
        transformations. The function extracts information for the current 
        tesselation basis, and then call the dynamic library functions compiled 
        from the cpp code which do the actual computations
        
    Arguments:
        op: tensorflow operation class. The class holds information about the
            input and output of the original operation we are trying to 
            differentiate
        grad: 4D-`Tensor` [dim, n_theta, 2, nb_points]. Incoming gradient that
            is propegated onwards by this layer. It can be viewed as the gradient
            vector in each point, for all thetas and for all parameters of each
            theta.
        
    Output:
        gradient: list of 2 elements. Each element corresponds to the gradient
        w.r.t the input to the original function _calc_trans(points, theta). 
        Since we are only interested in the gradient w.r.t. theta, the first
        element is None. The second is a `Matrix` [dim, n_theta] i.e. the gradient
        of each element in all theta vectors.
        
    """
    with tf.variable_scope('calc_grad'):
        # Grap input
        points = op.inputs[0] # 2 x nP
        theta = op.inputs[1] # n_theta x d
        n_theta = tf.shape(theta)[0]
    
        # Load file with basis
        file = load_basis()
        
        # Tessalation information
        nC = tf.cast(file['nC'], tf.int32)
        ncx = tf.cast(file['ncx'], tf.int32)
        ncy = tf.cast(file['ncy'], tf.int32)
        inc_x = tf.cast(file['inc_x'], tf.float32)
        inc_y = tf.cast(file['inc_y'], tf.float32)
        
        # Steps sizes
        nStepSolver = tf.cast(50, dtype = tf.int32)
    
        # Get cpab basis
        B = tf.cast(file['B'], tf.float32)
        Bs = tf.reshape(tf.transpose(B), (-1, nC, 2, 3))
        B = tf_repeat_matrix(B, n_theta)
        
        # Calculate the row-flatted affine transformations Avees 
        Avees = tf.matmul(B, tf.expand_dims(theta, 2))
        
        # Reshape into (ntheta, number of cells, 2, 3) tensor
        As = tf.reshape(Avees, shape = (n_theta, nC, 2, 3)) # n_theta x nC x 2 x 3
        
        # Call cuda code
        with tf.variable_scope('calcT_batch_grad_operator'):
            gradient = grad_op(points, As, Bs, nStepSolver,
                               ncx, ncy, inc_x, inc_y) # gradient: d x n_theta x 2 x n
        
        # Reduce into: d x 1 vector
        gradient = tf.reduce_sum(grad * gradient, axis = [2,3])
        gradient = tf.transpose(gradient)
                                  
        return [None, gradient]

#%%
#@function.Defun(tf.float32, tf.float32, func_name = 'tf_CPAB_transformer', python_grad_func = _calc_grad)
#def tf_CPAB_transformer(points, theta):
#    transformed_points = _calc_trans(points, theta)
#    return transformed_points



#%%
if __name__ == '__main__':
    from ddtn.transformers.setup_CPAB_transformer import setup_CPAB_transformer
    import numpy as np
    import matplotlib.pyplot as plt
    
    # Create basis
    s = setup_CPAB_transformer(2, 2, 
                               valid_outside=True, 
                               zero_boundary=False, 
                               override=True)
    
    # Sample parametrization and grid
    theta = 0.3*s.sample_theta_without_prior(1)
    points = s.sample_grid(20)
    
    # Convert to tf tensors
    theta_tf = tf.cast(theta, tf.float32, name='theta_cast')
    points_tf = tf.cast(points, tf.float32, name='point_cast')
    
    # Run computations
    sess = tf.Session(config=tf.ConfigProto(log_device_placement=True))
    #newpoints = sess.run(tf_CPAB_transformer(points_tf, theta_tf))
    #newpoints = np.reshape(newpoints, points.shape)
    
    # Show deformation
    #fig = plt.figure()
    #plt.plot(points[0], points[1], 'b.', label='original grid')
    #plt.plot(newpoints[0], newpoints[1], 'r.', label='deformed grid')
    #plt.legend(fontsize=15)
    
    # Show velocity field
    #s.visualize_vectorfield_arrow(theta.flatten())
#    
    # Calculate gradient
    #newpoints = tf_CPAB_transformer(points_tf, theta_tf)
    #grad_ana = tf.gradients(newpoints, [theta_tf])[0]
    #res = sess.run(grad_ana)
#    #grad_num = sess.run(tf.gradients(tf_CPAB_transformer_numeric_grad(points_tf, 
#    #                                theta_tf), [theta_tf])[0])
#    
    class op:
        def __init__(self, inputs):
            self.inputs = inputs
            
    operation = op([points_tf, theta_tf])
    grad = tf.ones((20, 1, 2, 20*20), tf.float32)
    gradient = _calc_grad(operation, grad)
    res = sess.run(gradient[1])
    
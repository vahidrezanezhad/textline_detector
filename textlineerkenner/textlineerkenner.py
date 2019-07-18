#! /usr/bin/env python3

__version__= '1.0'

import os
import sys
import cv2
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sys import getsizeof
import random
from tqdm import tqdm
from keras.models import model_from_json
from keras.models import load_model
import math
from shapely import geometry
from sklearn.cluster import KMeans
import gc
from keras import backend as K
import tensorflow as tf
from scipy.signal import find_peaks
from scipy.ndimage import gaussian_filter1d
import xml.etree.ElementTree as ET
import warnings
import argparse


with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    
__doc__=\
"""
tool to extract table form data from alto xml data
"""

class textlineerkenner:
    def __init__(self,image_dir,dir_out,dir_models):
        self.image_dir=image_dir
        self.dir_out=dir_out
        self.dir_models=dir_models
        try:
            self.f_name=image_dir.split('/')[len(image_dir.split('/'))-1]
            self.f_name=self.f_name.split('.')[0]
            print(self.f_name)
        except:
            self.f_name=self.f_name.split('.')[0]
        self.kernel = np.ones((5,5),np.uint8)
        self.model_page_dir=dir_models+'/model_page.h5'
        self.model_region_dir=dir_models+'/model_strukturerkennung.h5'
        self.model_textline_dir=dir_models+'/model_textline.h5'
        
        
        
        
    
    def find_polugons_size_filter(self,contours,median_area,scaler_up=1.2,scaler_down=0.8):
        found_polygons_early = list()
        
    
        for c in contours:
            if len(c) < 3:  # A polygon cannot have less than 3 points
                continue
                
            polygon = geometry.Polygon([point[0] for point in c])
            area = polygon.area
            # Check that polygon has area greater than minimal area
            if area >= median_area*scaler_down and area <= median_area*scaler_up:
                found_polygons_early.append(
                    np.array( [point for point in polygon.exterior.coords], dtype=np.uint ) )
        return found_polygons_early
    
    def filter_contours_area_of_image(self,image,contours,hirarchy,max_area,min_area):
        found_polygons_early = list()
    
        jv=0
        for c in contours:
            if len(c) < 3:  # A polygon cannot have less than 3 points
                continue
    
            polygon = geometry.Polygon([point[0] for point in c])
            area = polygon.area
            if area >=min_area*np.prod(image.shape[:2]) and area <=max_area*np.prod(image.shape[:2]):#and hirarchy[0][jv][3]==-1 :
                found_polygons_early.append(
                    np.array( [point for point in polygon.exterior.coords], dtype=np.uint ) )
            jv+=1
        return found_polygons_early
    
    
    def filter_contours_area_of_image_interiors(self,image,contours,hirarchy,max_area,min_area):
        found_polygons_early = list()
    
        jv=0
        for c in contours:
            if len(c) < 3:  # A polygon cannot have less than 3 points
                continue
    
            polygon = geometry.Polygon([point[0] for point in c])
            area = polygon.area
            if area >=min_area*np.prod(image.shape[:2]) and area <=max_area*np.prod(image.shape[:2]) and hirarchy[0][jv][3]!=-1 :
                #print(c[0][0][1])
                found_polygons_early.append(
                    np.array( [point for point in polygon.exterior.coords], dtype=np.uint ) )
            jv+=1
        return found_polygons_early
    
    def resize_image(self,img_in,input_height,input_width):
        return cv2.resize( img_in, ( input_width,input_height) ,interpolation=cv2.INTER_NEAREST)
    
    def resize_ann(self,seg_in,input_height,input_width):
        return cv2.resize(seg_in,(input_width,input_height),interpolation=cv2.INTER_NEAREST)
    def get_one_hot(self,seg,input_height,input_width,n_classes):
        seg=seg[:,:,0]
        seg_f=np.zeros((input_height, input_width,n_classes))
        for j in range(n_classes):
            seg_f[:,:,j]=(seg==j).astype(int)
        return seg_f
    
    
    
    
    def jaccard_distance_loss(self,y_true, y_pred, smooth=100):
        """
        Jaccard = (|X & Y|)/ (|X|+ |Y| - |X & Y|)
                = sum(|A*B|)/(sum(|A|)+sum(|B|)-sum(|A*B|))
        
        The jaccard distance loss is usefull for unbalanced datasets. This has been
        shifted so it converges on 0 and is smoothed to avoid exploding or disapearing
        gradient.
        
        Ref: https://en.wikipedia.org/wiki/Jaccard_index
        
        @url: https://gist.github.com/wassname/f1452b748efcbeb4cb9b1d059dce6f96
        @author: wassname
        """
        intersection = K.sum(K.abs(y_true * y_pred), axis=-1)
        sum_ = K.sum(K.abs(y_true) + K.abs(y_pred), axis=-1)
        jac = (intersection + smooth) / (sum_ - intersection + smooth)
        return (1 - jac) * smooth
    def soft_dice_loss(self,y_true, y_pred, epsilon=1e-6): 
        ''' 
        Soft dice loss calculation for arbitrary batch size, number of classes, and number of spatial dimensions.
        Assumes the `channels_last` format.
      
        # Arguments
            y_true: b x X x Y( x Z...) x c One hot encoding of ground truth
            y_pred: b x X x Y( x Z...) x c Network output, must sum to 1 over c channel (such as after softmax) 
            epsilon: Used for numerical stability to avoid divide by zero errors
        
        # References
            V-Net: Fully Convolutional Neural Networks for Volumetric Medical Image Segmentation 
            https://arxiv.org/abs/1606.04797
            More details on Dice loss formulation 
            https://mediatum.ub.tum.de/doc/1395260/1395260.pdf (page 72)
            
            Adapted from https://github.com/Lasagne/Recipes/issues/99#issuecomment-347775022
        '''
        
        # skip the batch and class axis for calculating Dice score
        axes = tuple(range(1, len(y_pred.shape)-1))
        
        numerator = 2. * K.sum(y_pred * y_true, axes)
    
        denominator = K.sum(K.square(y_pred) + K.square(y_true), axes)
        return 1.00 - K.mean(numerator / (denominator + epsilon)) # average over classes and batch
    
    
    def weighted_categorical_crossentropy(self,weights=None):
        """ weighted_categorical_crossentropy
    
            Args:
                * weights<ktensor|nparray|list>: crossentropy weights
            Returns:
                * weighted categorical crossentropy function
        """
    
        def loss(y_true, y_pred):
            labels_floats = tf.cast(y_true, tf.float32)
            per_pixel_loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=labels_floats,logits=y_pred)
        
            if weights is not None:
                weight_mask = tf.maximum(tf.reduce_max(tf.constant(
                    np.array(weights, dtype=np.float32)[None, None, None])
                    * labels_floats, axis=-1), 1.0)
                per_pixel_loss = per_pixel_loss * weight_mask[:, :, :, None]
            return tf.reduce_mean(per_pixel_loss)
        return loss
    
    
    def seg_metrics(self,y_true, y_pred, metric_name, metric_type='standard', drop_last = True, mean_per_class=False, verbose=False):
        flag_soft = (metric_type == 'soft')
        flag_naive_mean = (metric_type == 'naive')
        
        # always assume one or more classes
        num_classes = K.shape(y_true)[-1]
            
        if not flag_soft:
            # get one-hot encoded masks from y_pred (true masks should already be one-hot)
            y_pred = K.one_hot(K.argmax(y_pred), num_classes)
            y_true = K.one_hot(K.argmax(y_true), num_classes)
    
        # if already one-hot, could have skipped above command
        # keras uses float32 instead of float64, would give error down (but numpy arrays or keras.to_categorical gives float64)
        y_true = K.cast(y_true, 'float32')
        y_pred = K.cast(y_pred, 'float32')
    
        # intersection and union shapes are batch_size * n_classes (values = area in pixels)
        axes = (1,2) # W,H axes of each image
        intersection = K.sum(K.abs(y_true * y_pred), axis=axes)
        mask_sum = K.sum(K.abs(y_true), axis=axes) + K.sum(K.abs(y_pred), axis=axes)
        union = mask_sum  - intersection # or, np.logical_or(y_pred, y_true) for one-hot
    
        smooth = .001
        iou = (intersection + smooth) / (union + smooth)
        dice = 2 * (intersection + smooth)/(mask_sum + smooth)
    
        metric = {'iou': iou, 'dice': dice}[metric_name]
    
        # define mask to be 0 when no pixels are present in either y_true or y_pred, 1 otherwise
        mask =  K.cast(K.not_equal(union, 0), 'float32')
        
        if drop_last:
            metric = metric[:,:-1]
            mask = mask[:,:-1]
        
        if verbose:
            print('intersection, union')
            print(K.eval(intersection), K.eval(union))
            print(K.eval(intersection/union))
        
        # return mean metrics: remaining axes are (batch, classes)
        if flag_naive_mean:
            return K.mean(metric)
    
        # take mean only over non-absent classes
        class_count = K.sum(mask, axis=0)
        non_zero = tf.greater(class_count, 0)
        non_zero_sum = tf.boolean_mask(K.sum(metric * mask, axis=0), non_zero)
        non_zero_count = tf.boolean_mask(class_count, non_zero)
        
        if verbose:
            print('Counts of inputs with class present, metrics for non-absent classes')
            print(K.eval(class_count), K.eval(non_zero_sum / non_zero_count))
            
        return K.mean(non_zero_sum / non_zero_count)

    def mean_iou(self,y_true, y_pred, **kwargs):
        return self.seg_metrics(y_true, y_pred, metric_name='iou', **kwargs)
    
    def Mean_IOU(self,y_true, y_pred):
        nb_classes = K.int_shape(y_pred)[-1]
        iou = []
        true_pixels = K.argmax(y_true, axis=-1)
        pred_pixels = K.argmax(y_pred, axis=-1)
        void_labels = K.equal(K.sum(y_true, axis=-1), 0)
        for i in range(0, nb_classes): # exclude first label (background) and last label (void)
            true_labels = K.equal(true_pixels, i)# & ~void_labels
            pred_labels = K.equal(pred_pixels, i)# & ~void_labels
            inter = tf.to_int32(true_labels & pred_labels)
            union = tf.to_int32(true_labels | pred_labels)
            legal_batches = K.sum(tf.to_int32(true_labels), axis=1)>0
            ious = K.sum(inter, axis=1)/K.sum(union, axis=1)
            iou.append(K.mean(tf.gather(ious, indices=tf.where(legal_batches)))) # returns average IoU of the same objects
        iou = tf.stack(iou)
        legal_labels = ~tf.debugging.is_nan(iou)
        iou = tf.gather(iou, indices=tf.where(legal_labels))
        return K.mean(iou)
    def IoU(self,Yi,y_predi):
        ## mean Intersection over Union
        ## Mean IoU = TP/(FN + TP + FP)
    
        IoUs = []
        Nclass = np.unique(Yi)
        for c in Nclass:
            TP = np.sum( (Yi == c)&(y_predi==c) )
            FP = np.sum( (Yi != c)&(y_predi==c) )
            FN = np.sum( (Yi == c)&(y_predi != c)) 
            IoU = TP/float(TP + FP + FN)
            print("class {:02.0f}: #TP={:6.0f}, #FP={:6.0f}, #FN={:5.0f}, IoU={:4.3f}".format(c,TP,FP,FN,IoU))
            IoUs.append(IoU)
        mIoU = np.mean(IoUs)
        print("_________________")
        print("Mean IoU: {:4.3f}".format(mIoU))
        return mIoU

    def IoU_case(self,Yi,y_predi,n_classes):
        ## mean Intersection over Union
        ## Mean IoU = TP/(FN + TP + FP)
    
        IoUs = []
    
        Nclass = n_classes
        for c in range(Nclass):
            TP = np.sum( (Yi == c)&(y_predi==c) )
            FP = np.sum( (Yi != c)&(y_predi==c) )
            FN = np.sum( (Yi == c)&(y_predi != c)) 
            IoUs.append(np.array([TP, FP, FN ]) )
        return IoUs




    def color_images(self,seg, n_classes):
        ann_u=range(n_classes)
        if len(np.shape(seg))==3:
            seg=seg[:,:,0]
            
        seg_img=np.zeros((np.shape(seg)[0],np.shape(seg)[1],3)).astype(np.uint8)
        colors=sns.color_palette("hls", n_classes)
        
        for c in ann_u:
            c=int(c)
            segl=(seg==c)
            seg_img[:,:,0]=segl*c
            seg_img[:,:,1]=segl*c
            seg_img[:,:,2]=segl*c
        return seg_img
    
    
    def color_images_diva(self,seg, n_classes):
        ann_u=range(n_classes)
        if len(np.shape(seg))==3:
            seg=seg[:,:,0]
            
        seg_img=np.zeros((np.shape(seg)[0],np.shape(seg)[1],3)).astype(float)
        #colors=sns.color_palette("hls", n_classes)
        colors=[[1,0,0],[8,0,0],[2,0,0],[4,0,0]]
        
        for c in ann_u:
            c=int(c)
            segl=(seg==c)
            seg_img[:,:,0][seg==c]=colors[c][0]#segl*(colors[c][0])
            seg_img[:,:,1][seg==c]=colors[c][1]#seg_img[:,:,1]=segl*(colors[c][1])
            seg_img[:,:,2][seg==c]=colors[c][2]#seg_img[:,:,2]=segl*(colors[c][2])
        return seg_img
    
    
    
    def rotate_image(self,img_patch,slope):
        (h, w) = img_patch.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, slope, 1.0)
        return cv2.warpAffine(img_patch, M, (w, h),flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    
    
    def cleaning_probs(self,probs: np.ndarray, sigma: float) -> np.ndarray:
        # Smooth
        if sigma > 0.:
            return cv2.GaussianBlur(probs, (int(3*sigma)*2+1, int(3*sigma)*2+1), sigma)
        elif sigma == 0.:
            return cv2.fastNlMeansDenoising((probs*255).astype(np.uint8), h=20)/255
        else:  # Negative sigma, do not do anything
            return probs
        
    def crop_image_inside_box(self,box,img_org_copy):
        image_box=img_org_copy[box[1]:box[1]+box[3] , box[0]:box[0]+box[2] ]
        return image_box , [box[1],box[1]+box[3] , box[0],box[0]+box[2]]

    def otsu_copy(self,img):
        img_r=np.zeros(img.shape)
        img1=img[:,:,0]
        img2=img[:,:,1]
        img3=img[:,:,2]
        #print(img.min())
        #print(img[:,:,0].min())
        #blur = cv2.GaussianBlur(img,(5,5))
        #ret3,th3 = cv2.threshold(blur,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        retval1, threshold1 = cv2.threshold(img1, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        retval2, threshold2 = cv2.threshold(img2, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        retval3, threshold3 = cv2.threshold(img3, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        
    
        img_r[:,:,0]=threshold1
        img_r[:,:,1]=threshold2
        img_r[:,:,2]=threshold3
        return img_r
    def get_image_and_scales(self):
        self.image=cv2.imread(self.image_dir)
        self.height_org=self.image.shape[0]
        self.width_org=self.image.shape[1]
        
        
        if self.image.shape[0]<1000:
            self.img_hight_int=1800
            self.img_width_int=int(self.img_hight_int*self.image.shape[1]/float(self.image.shape[0])) 
            
        elif self.image.shape[0]<2000 and self.image.shape[0]>=1000 :
            self.img_hight_int=3500
            self.img_width_int=int(self.img_hight_int*self.image.shape[1]/float(self.image.shape[0])) 
            
        elif self.image.shape[0]<3000 and self.image.shape[0]>=2000 :
            self.img_hight_int=4000
            self.img_width_int=int(self.img_hight_int*self.image.shape[1]/float(self.image.shape[0])) 
            
        elif self.image.shape[0]<4000 and self.image.shape[0]>=3000 :
            self.img_hight_int=4500
            self.img_width_int=int(self.img_hight_int*self.image.shape[1]/float(self.image.shape[0])) 
            
        else:
            self.img_hight_int=self.image.shape[0]
            self.img_width_int=self.image.shape[1]

        
        self.scale_y=self.img_hight_int/float(self.image.shape[0])
        self.scale_x=self.img_width_int/float(self.image.shape[1])
        
        self.image=self.resize_image(self.image,self.img_hight_int,self.img_width_int)
        
        
    
    def start_new_session_and_model(self,model_dir):
        config = tf.ConfigProto()
        config.gpu_options.allow_growth=True
    
        session = tf.InteractiveSession()
        model = load_model(model_dir, custom_objects={'mean_iou': self.mean_iou,
                                                      'soft_dice_loss': self.soft_dice_loss,'jaccard_distance_loss':self.jaccard_distance_loss,'Mean_IOU':self.Mean_IOU})
    
        return model,session
    def extract_page(self):
        model_page,session_page=self.start_new_session_and_model(self.model_page_dir)
        
        img_height_page=model_page.layers[len(model_page.layers)-1].output_shape[1]
        img_width_page=model_page.layers[len(model_page.layers)-1].output_shape[2]
        n_classes_page=model_page.layers[len(model_page.layers)-1].output_shape[3]
        
        img_org_copy=self.image.copy()
        
        
        for ii in range(40):
            img = cv2.GaussianBlur(self.image,(5,5),0)
            
        ###img=self.image.astype(np.uint8)
        #img=self.otsu_copy(img)

        
        img=img/255.0
        img=self.resize_image(img,img_height_page,img_width_page)
        
    
        label_p_pred=model_page.predict(
            img.reshape(1,img.shape[0],img.shape[1],img.shape[2]))
    
        seg=np.argmax(label_p_pred,axis=3)[0]
        seg_color=self.color_images(seg,n_classes_page)
    
    
        imgs = seg_color#/np.max(seg_color)*255#np.repeat(seg_color[:, :, np.newaxis], 3, axis=2)
        
    
        imgs=self.resize_image(imgs,img_org_copy.shape[0],img_org_copy.shape[1])
        
        
    
        imgs=imgs.astype(np.uint8)
        imgray = cv2.cvtColor(imgs, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(imgray, 0, 255, 0)
        

        thresh = cv2.dilate(thresh,self.kernel,iterations = 30)
        contours,_=cv2.findContours(thresh,cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)

    
        cnt_size=np.array([cv2.contourArea(contours[j]) for j in range(len(contours))])
    
        cnt=contours[np.argmax(cnt_size)]
    
        x,y,w,h = cv2.boundingRect(cnt)
        
        box=[x,y,w,h]

    
        croped_page,page_coord=self.crop_image_inside_box(box,img_org_copy)
        
        session_page.close()
        del model_page
        del session_page
        
        gc.collect()
        return croped_page,page_coord
    
    
    def extract_text_regions(self,img):
        model_region,session_region=self.start_new_session_and_model(self.model_region_dir)
        
        img_height_region=model_region.layers[len(model_region.layers)-1].output_shape[1]
        img_width_region=model_region.layers[len(model_region.layers)-1].output_shape[2]
        n_classes=model_region.layers[len(model_region.layers)-1].output_shape[3]
        margin=True
        if margin:
  
            width=img_width_region
            height=img_height_region
    
            #offset=int(.1*width)
            offset=int(0.03*width)
    
            width_mid=width-2*offset
            height_mid=height-2*offset
    
            img=self.otsu_copy(img)
            img=img.astype(np.uint8)
            ###img = cv2.medianBlur(img,5)
            
            #img = cv2.medianBlur(img,5)
    
            #img=cv2.bilateralFilter(img,9,75,75)
            #img=cv2.bilateralFilter(img,9,75,75)
    
            img=img/255.0
    
    
            img_h=img.shape[0]
            img_w=img.shape[1]
    
            prediction_true=np.zeros((img_h,img_w,3))
            mask_true=np.zeros((img_h,img_w))
            nxf=img_w/float(width_mid)
            nyf=img_h/float(height_mid)
    
            if nxf>int(nxf):
                nxf=int(nxf)+1
            else:
                nxf=int(nxf)
                
            if nyf>int(nyf):
                nyf=int(nyf)+1
            else:
                nyf=int(nyf)
    
            for i in range(nxf):
                for j in range(nyf):
    
                    if i==0:
                        index_x_d=i*width_mid
                        index_x_u=index_x_d+width#(i+1)*width
                    elif i>0:
                        index_x_d=i*width_mid
                        index_x_u=index_x_d+width#(i+1)*width
    
                    if j==0:
                        index_y_d=j*height_mid
                        index_y_u=index_y_d+height#(j+1)*height
                    elif j>0:
                        index_y_d=j*height_mid
                        index_y_u=index_y_d+height#(j+1)*height
    
                    if index_x_u>img_w:
                        index_x_u=img_w
                        index_x_d=img_w-width
                    if index_y_u>img_h:
                        index_y_u=img_h
                        index_y_d=img_h-height
    
    
                    img_patch=img[index_y_d:index_y_u,index_x_d:index_x_u,:]
    
    
    
                    label_p_pred=model_region.predict(
                        img_patch.reshape(1,img_patch.shape[0],img_patch.shape[1],img_patch.shape[2]))
    
    
                    seg=np.argmax(label_p_pred,axis=3)[0]
    
                    seg_color=np.repeat(seg[:, :, np.newaxis], 3, axis=2)

    
                    seg_color=seg_color[offset:seg_color.shape[0]-offset,offset:seg_color.shape[1]-offset,:]
                    seg=seg[offset:seg.shape[0]-offset,offset:seg.shape[1]-offset]
    
    
                    mask_true[index_y_d+offset:index_y_u-offset,index_x_d+offset:index_x_u-offset]=seg
                    prediction_true[index_y_d+offset:index_y_u-offset,index_x_d+offset:index_x_u-offset,:]=seg_color
    
            prediction_plot=np.zeros(prediction_true.shape)
    
            prediction_plot[:,:,0][prediction_true[:,:,0]==1]=255
            prediction_plot[:,:,1][prediction_true[:,:,1]==1]=255
            prediction_plot[:,:,2][prediction_true[:,:,2]==1]=255
            
            prediction_plot[:,:,0][prediction_true[:,:,0]==2]=255
            prediction_plot[:,:,1][prediction_true[:,:,1]==2]=0
            prediction_plot[:,:,2][prediction_true[:,:,2]==2]=0
            
            prediction_plot[:,:,0][prediction_true[:,:,0]==3]=0
            prediction_plot[:,:,1][prediction_true[:,:,1]==3]=255
            prediction_plot[:,:,2][prediction_true[:,:,2]==3]=0
            
            prediction_plot[:,:,0][prediction_true[:,:,0]==4]=0
            prediction_plot[:,:,1][prediction_true[:,:,1]==4]=0
            prediction_plot[:,:,2][prediction_true[:,:,2]==4]=255
            
            prediction_plot[:,:,0][prediction_true[:,:,0]==5]=0
            prediction_plot[:,:,1][prediction_true[:,:,1]==5]=255
            prediction_plot[:,:,2][prediction_true[:,:,2]==5]=255
            prediction_true=prediction_true.astype(np.uint8)
            session_region.close()
            
            
            del model_region
            del session_region
            gc.collect()
            return prediction_true

    def get_text_region_contours_and_boxes(self,image):
        rgb_class=(1,1,1)
        mask = np.all(image == rgb_class, axis=-1)
    
        image = np.repeat(mask[:, :, np.newaxis], 3, axis=2)*255
        image=image.astype(np.uint8)
    
    
    
        image = cv2.morphologyEx(image, cv2.MORPH_OPEN, self.kernel)
        image = cv2.morphologyEx(image, cv2.MORPH_CLOSE, self.kernel)
        image = cv2.erode(image,self.kernel,iterations = 3)
    
        image = cv2.dilate(image,self.kernel,iterations = 3)
    
    
        imgray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


        _, thresh = cv2.threshold(imgray, 0, 255, 0)

    
        contours,hirarchy=cv2.findContours(thresh.copy(), cv2.cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
    
        
        
        #commenst_contours=self.filter_contours_area_of_image(thresh,contours,hirarchy,max_area=0.0002,min_area=0.0001)
        main_contours=self.filter_contours_area_of_image(thresh,contours,hirarchy,max_area=1,min_area=0.0002)
    
        img_comm=np.zeros(thresh.shape)
        img_comm_in=cv2.fillPoly(img_comm, pts =main_contours, color=(255,255,255))
    
        img_comm_in = np.repeat(img_comm_in[:, :, np.newaxis], 3, axis=2)
    
        
    
        
    
    
        img_comm_in=img_comm_in.astype(np.uint8)
        #img_comm_in_de=self.deskew_images(img_comm_in)

    
        imgray = cv2.cvtColor(img_comm_in, cv2.COLOR_BGR2GRAY)

        _, thresh = cv2.threshold(imgray, 0, 255, 0)
    
        contours,hirarchy=cv2.findContours(thresh.copy(), cv2.cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        
        
        boxes=[]
        contours_new=[]
        for jj in range(len(contours)):
            if hirarchy[0][jj][2]==-1:          
                x,y,w,h = cv2.boundingRect(contours[jj])
                boxes.append([x,y,w,h])
                contours_new.append(contours[jj])
    
        return boxes,contours_new

    def get_all_image_patches_based_on_text_regions(self,boxes,image_page):
        self.all_text_images=[]
        self.all_box_coord=[]
        for jk in range(len(boxes)):
            crop_img,crop_coor=self.crop_image_inside_box(boxes[jk],image_page)
            self.all_text_images.append(crop_img)
            self.all_box_coord.append(crop_coor)        

    def textline_contours(self,img):
        model_textline,session_textline=self.start_new_session_and_model(self.model_textline_dir)
        img_height_textline=model_textline.layers[len(model_textline.layers)-1].output_shape[1]
        img_width_textline=model_textline.layers[len(model_textline.layers)-1].output_shape[2]
        n_classes=model_textline.layers[len(model_textline.layers)-1].output_shape[3]
        
        img_org=img.copy()
        
        if img.shape[0]<img_height_textline:
            img=cv2.resize( img, ( img.shape[1],img_width_textline) ,interpolation=cv2.INTER_NEAREST)
    
        if img.shape[1]<img_width_textline:
            img=cv2.resize( img, ( img_height_textline,img.shape[0]) ,interpolation=cv2.INTER_NEAREST)
    
        margin=False
        if not margin:
    
    
            width=img_width_textline
            height=img_height_textline

            
            
            img=self.otsu_copy(img)
            img=img.astype(np.uint8)
            img = cv2.medianBlur(img,5)
            img=img/255.0
    
    
            img_h=img.shape[0]
            img_w=img.shape[1]
    
            prediction_true=np.zeros((img_h,img_w,3))
            mask_true=np.zeros((img_h,img_w))
            nxf=img_w/float(width)
            nyf=img_h/float(height)
    
            if nxf>int(nxf):
                nxf=int(nxf)+1
            else:
                nxf=int(nxf)
                
            if nyf>int(nyf):
                nyf=int(nyf)+1
            else:
                nyf=int(nyf)
    
            for i in range(nxf):
                for j in range(nyf):
                    index_x_d=i*width
                    index_x_u=(i+1)*width
    
                    index_y_d=j*height
                    index_y_u=(j+1)*height
    
                    if index_x_u>img_w:
                        index_x_u=img_w
                        index_x_d=img_w-width
                    if index_y_u>img_h:
                        index_y_u=img_h
                        index_y_d=img_h-height
    
    
                    img_patch=img[index_y_d:index_y_u,index_x_d:index_x_u,:]
    
                    label_p_pred=model_textline.predict(img_patch.reshape(1,img_patch.shape[0],img_patch.shape[1],img_patch.shape[2]) )
                    seg=np.argmax(label_p_pred,axis=3)[0]
                    seg_color=self.color_images(seg,n_classes)
                    mask_true[index_y_d:index_y_u,index_x_d:index_x_u]=seg
                    prediction_true[index_y_d:index_y_u,index_x_d:index_x_u,:]=seg_color
    

            y_predi = mask_true
            y_predi=cv2.resize( y_predi, ( img_org.shape[1],img_org.shape[0]) ,interpolation=cv2.INTER_NEAREST)
        return y_predi
    def get_textlines_for_each_textregions(self,textline_mask_tot,boxes):
        textline_mask_tot = cv2.erode(textline_mask_tot,self.kernel,iterations = 1)
        self.area_of_cropped=[]
        self.all_text_region_raw=[]
        for jk in range(len(boxes)):
            crop_img,crop_coor=self.crop_image_inside_box(boxes[jk],np.repeat(textline_mask_tot[:, :, np.newaxis], 3, axis=2))
            self.all_text_region_raw.append(crop_img[:,:,0])
            self.area_of_cropped.append(crop_img.shape[0]*crop_img.shape[1])
            


    def seperate_lines(self,mada,contour_text_interest,thetha):
        (h, w) = mada.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, -thetha, 1.0)
        x_d=M[0,2]
        y_d=M[1,2]
    

        thetha=thetha/180.*np.pi
        rotation_matrix=np.array([[ np.cos(thetha), -np.sin(thetha)], [ np.sin(thetha), np.cos(thetha)]])
        contour_text_interest_copy=contour_text_interest.copy()
        
        
        x_cont=contour_text_interest[:,0,0]
        y_cont=contour_text_interest[:,0,1]
        x_cont=x_cont-np.min(x_cont)
        y_cont=y_cont-np.min(y_cont)
        
        x_min_cont=0
        x_max_cont=mada.shape[1]
        y_min_cont=0
        y_max_cont=mada.shape[0]
        
        xv=np.linspace(x_min_cont,x_max_cont,1000)
        
        mada_n=mada.sum(axis=1)
        

    
        first_nonzero=0#(next((i for i, x in enumerate(mada_n) if x), None))
  
        y=mada_n[:]#[first_nonzero:last_nonzero]
        y_help=np.zeros(len(y)+40)
        y_help[20:len(y)+20]=y
        x=np.array( range(len(y)) )
    
    
        peaks_real, _ = find_peaks(gaussian_filter1d(y, 3), height=0)
        if len(peaks_real)<=2 and len(peaks_real)>1:
            sigma_gaus=16
        else:
            sigma_gaus=8
    
    
        z= gaussian_filter1d(y_help, sigma_gaus)
        zneg_rev=-y_help+np.max(y_help)
        zneg=np.zeros(len(zneg_rev)+40)
        zneg[20:len(zneg_rev)+20]=zneg_rev
        zneg= gaussian_filter1d(zneg, sigma_gaus)

    
        peaks, _ = find_peaks(z, height=0)
        peaks_neg, _ = find_peaks(zneg, height=0)
    
        peaks_neg=peaks_neg-20-20
        peaks=peaks-20
        
        for jj in range(len(peaks_neg)):
            if peaks_neg[jj]>len(x)-1:
                peaks_neg[jj]=len(x)-1
                
        for jj in range(len(peaks)):
            if peaks[jj]>len(x)-1:
                peaks[jj]=len(x)-1


        
        textline_boxes=[]
        textline_boxes_rot=[]
        

        if len(peaks_neg)==len(peaks)+1 and len(peaks)>=3:
            for jj in range(len(peaks)):
                dis_to_next_up=abs(peaks[jj]-peaks_neg[jj])
                dis_to_next_down=abs(peaks[jj]-peaks_neg[jj+1])
    
                point_up=peaks[jj]+first_nonzero-int(1.1*dis_to_next_up)##+int(dis_to_next_up*1./4.0)
                point_down=peaks[jj]+first_nonzero+int(1.1*dis_to_next_down)###-int(dis_to_next_down*1./4.0)
                
                point_down_narrow=peaks[jj]+first_nonzero+int(1.1*dis_to_next_down)###-int(dis_to_next_down*1./2)
                
                if point_down>=mada.shape[0]:
                    point_down=mada.shape[0]-2
                    
                if point_down_narrow>=mada.shape[0]:
                    point_down_narrow=mada.shape[0]-2
    
    
    
                distances=[cv2.pointPolygonTest(contour_text_interest_copy, (xv[mj],peaks[jj]+first_nonzero),True) for mj in range(len(xv))]
                distances=np.array(distances)
    
                xvinside=xv[distances>=0]
                
                if len(xvinside)==0:
                    x_min=x_min_cont
                    x_max=x_max_cont
                else:
                    x_min=np.min(xvinside)#max(x_min_interest,x_min_cont)
                    x_max=np.max(xvinside)#min(x_max_interest,x_max_cont)
    
                
                p1=np.dot(rotation_matrix,[ int(x_min) , int(point_up) ])
                p2=np.dot(rotation_matrix,[ int(x_max) , int(point_up) ])
                p3=np.dot(rotation_matrix,[ int(x_max) , int(point_down) ])
                p4=np.dot(rotation_matrix,[ int(x_min) , int(point_down) ])
                
                x_min_rot1,point_up_rot1=p1[0]+x_d,p1[1]+y_d
                x_max_rot2,point_up_rot2=p2[0]+x_d,p2[1]+y_d
                x_max_rot3,point_down_rot3=p3[0]+x_d,p3[1]+y_d
                x_min_rot4,point_down_rot4=p4[0]+x_d,p4[1]+y_d
                
                
                textline_boxes_rot.append( np.array( [ [ int(x_min_rot1) , int(point_up_rot1) ] , 
                                                  [ int(x_max_rot2) , int(point_up_rot2) ] ,
                                                 [ int(x_max_rot3) , int(point_down_rot3) ] ,
                                               [ int(x_min_rot4) , int(point_down_rot4) ]] ) )
                
                textline_boxes.append( np.array( [ [ int(x_min) , int(point_up) ] , 
                                                  [ int(x_max) , int(point_up) ] ,
                                                 [ int(x_max) , int(point_down) ] ,
                                               [ int(x_min) , int(point_down) ]] ) )
                
        elif len(peaks)<1:
            pass
        
        elif len(peaks)==1:
            x_min=x_min_cont
            x_max=x_max_cont
    
            y_min=y_min_cont
            y_max=y_max_cont
            
            p1=np.dot(rotation_matrix,[ int(x_min) , int(y_min) ])
            p2=np.dot(rotation_matrix,[ int(x_max) , int(y_min) ])
            p3=np.dot(rotation_matrix,[ int(x_max) , int(y_max) ])
            p4=np.dot(rotation_matrix,[ int(x_min) , int(y_max) ])
    
            x_min_rot1,point_up_rot1=p1[0]+x_d,p1[1]+y_d
            x_max_rot2,point_up_rot2=p2[0]+x_d,p2[1]+y_d
            x_max_rot3,point_down_rot3=p3[0]+x_d,p3[1]+y_d
            x_min_rot4,point_down_rot4=p4[0]+x_d,p4[1]+y_d
    
    
            textline_boxes_rot.append( np.array( [ [ int(x_min_rot1) , int(point_up_rot1) ] , 
                                              [ int(x_max_rot2) , int(point_up_rot2) ] ,
                                             [ int(x_max_rot3) , int(point_down_rot3) ] ,
                                           [ int(x_min_rot4) , int(point_down_rot4) ]] ) )
    
            textline_boxes.append( np.array( [ [ int(x_min) , int(y_min) ] , 
                                              [ int(x_max) , int(y_min) ] ,
                                             [ int(x_max) , int(y_max) ] ,
                                           [ int(x_min) , int(y_max) ]] ) )
            
            
            
        elif len(peaks)==2 :
            dis_to_next=np.abs(peaks[1]-peaks[0])
            for jj in range(len(peaks)):
                if jj==0:
                    point_up=peaks[jj]+first_nonzero-int(1./1.9*dis_to_next)
                    if point_up<0:
                        point_up=1
                    point_down=peaks[jj]+first_nonzero+int(1./1.9*dis_to_next)
                elif jj==1:
                    point_down=peaks[jj]+first_nonzero+int(1./1.9*dis_to_next)
                    if point_down>=mada.shape[0]:
                        point_down=mada.shape[0]-2
                    point_up=peaks[jj]+first_nonzero-int(1./1.9*dis_to_next)
    
    
    
    
    
                distances=[cv2.pointPolygonTest(contour_text_interest_copy, (xv[mj],peaks[jj]+first_nonzero),True) for mj in range(len(xv))]
                distances=np.array(distances)
    
                xvinside=xv[distances>=0]
                
                if len(xvinside)==0:
                    x_min=x_min_cont
                    x_max=x_max_cont
                else:
                    x_min=np.min(xvinside)#max(x_min_interest,x_min_cont)
                    x_max=np.max(xvinside)#min(x_max_interest,x_max_cont)
                    
    
                p1=np.dot(rotation_matrix,[ int(x_min) , int(point_up) ])
                p2=np.dot(rotation_matrix,[ int(x_max) , int(point_up) ])
                p3=np.dot(rotation_matrix,[ int(x_max) , int(point_down) ])
                p4=np.dot(rotation_matrix,[ int(x_min) , int(point_down) ])
                
                x_min_rot1,point_up_rot1=p1[0]+x_d,p1[1]+y_d
                x_max_rot2,point_up_rot2=p2[0]+x_d,p2[1]+y_d
                x_max_rot3,point_down_rot3=p3[0]+x_d,p3[1]+y_d
                x_min_rot4,point_down_rot4=p4[0]+x_d,p4[1]+y_d
                
                
                textline_boxes_rot.append( np.array( [ [ int(x_min_rot1) , int(point_up_rot1) ] , 
                                                  [ int(x_max_rot2) , int(point_up_rot2) ] ,
                                                 [ int(x_max_rot3) , int(point_down_rot3) ] ,
                                               [ int(x_min_rot4) , int(point_down_rot4) ]] ) )
    
                textline_boxes.append( np.array( [ [ int(x_min) , int(point_up) ] , 
                                                  [ int(x_max) , int(point_up) ] ,
                                                 [ int(x_max) , int(point_down) ] ,
                                                 [ int(x_min) , int(point_down) ]] ) )
        else:        
            for jj in range(len(peaks)):
    
    
                if jj==0:
                    dis_to_next=peaks[jj+1]-peaks[jj]
                    #point_up=peaks[jj]+first_nonzero-int(1./3*dis_to_next)
                    point_up=peaks[jj]+first_nonzero-int(1./1.9*dis_to_next)
                    if point_up<0:
                        point_up=1
                    #point_down=peaks[jj]+first_nonzero+int(1./3*dis_to_next)
                    point_down=peaks[jj]+first_nonzero+int(1./1.9*dis_to_next)
                elif jj==len(peaks)-1:
                    dis_to_next=peaks[jj]-peaks[jj-1]
                    #point_down=peaks[jj]+first_nonzero+int(1./3*dis_to_next)
                    point_down=peaks[jj]+first_nonzero+int(1./1.9*dis_to_next)
                    if point_down>=mada.shape[0]:
                        point_down=mada.shape[0]-2
                    #point_up=peaks[jj]+first_nonzero-int(1./3*dis_to_next)
                    point_up=peaks[jj]+first_nonzero-int(1./1.9*dis_to_next)
                else:
                    dis_to_next_down=peaks[jj+1]-peaks[jj]
                    dis_to_next_up=peaks[jj]-peaks[jj-1]
                
                    point_up=peaks[jj]+first_nonzero-int(1./1.9*dis_to_next_up)
                    point_down=peaks[jj]+first_nonzero+int(1./1.9*dis_to_next_down)
    
    
    
    
                distances=[cv2.pointPolygonTest(contour_text_interest_copy, (xv[mj],peaks[jj]+first_nonzero),True) for mj in range(len(xv))]
                distances=np.array(distances)
    
                xvinside=xv[distances>=0]
                
                if len(xvinside)==0:
                    x_min=x_min_cont
                    x_max=x_max_cont
                else:
                    x_min=np.min(xvinside)#max(x_min_interest,x_min_cont)
                    x_max=np.max(xvinside)#min(x_max_interest,x_max_cont)
                    
    
                p1=np.dot(rotation_matrix,[ int(x_min) , int(point_up) ])
                p2=np.dot(rotation_matrix,[ int(x_max) , int(point_up) ])
                p3=np.dot(rotation_matrix,[ int(x_max) , int(point_down) ])
                p4=np.dot(rotation_matrix,[ int(x_min) , int(point_down) ])
                
                x_min_rot1,point_up_rot1=p1[0]+x_d,p1[1]+y_d
                x_max_rot2,point_up_rot2=p2[0]+x_d,p2[1]+y_d
                x_max_rot3,point_down_rot3=p3[0]+x_d,p3[1]+y_d
                x_min_rot4,point_down_rot4=p4[0]+x_d,p4[1]+y_d
                
                
                textline_boxes_rot.append( np.array( [ [ int(x_min_rot1) , int(point_up_rot1) ] , 
                                                  [ int(x_max_rot2) , int(point_up_rot2) ] ,
                                                 [ int(x_max_rot3) , int(point_down_rot3) ] ,
                                               [ int(x_min_rot4) , int(point_down_rot4) ]] ) )
    
                textline_boxes.append( np.array( [ [ int(x_min) , int(point_up) ] , 
                                                  [ int(x_max) , int(point_up) ] ,
                                                 [ int(x_max) , int(point_down) ] ,
                                                 [ int(x_min) , int(point_down) ]] ) )

            
        mada_new = np.zeros( (mada.shape[0],mada.shape[1],3) ) 
        mada_new=cv2.fillPoly(mada_new, pts =textline_boxes, color=(255,255,255))
    
        mada_new=mada_new.astype(np.uint8)
        return mada_new,peaks,textline_boxes_rot

    
    def textline_contours_postprocessing(self,textline_mask,img_patch,slope,contour_text_interest,box_ind):
        
        textline_mask=np.repeat(textline_mask[:, :, np.newaxis], 3, axis=2)*255
    
        textline_mask=textline_mask.astype(np.uint8)
        kernel = np.ones((5,5),np.uint8)
        textline_mask = cv2.morphologyEx(textline_mask, cv2.MORPH_OPEN, kernel)
        textline_mask = cv2.morphologyEx(textline_mask, cv2.MORPH_CLOSE, kernel)
        textline_mask = cv2.erode(textline_mask,kernel,iterations = 1)
        imgray = cv2.cvtColor(textline_mask, cv2.COLOR_BGR2GRAY)
    
    
        _, thresh = cv2.threshold(imgray, 0, 255, 0)
    
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    
    
    
        contours,hirarchy=cv2.findContours(thresh.copy(), cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
        
    
    
        commenst_contours=self.filter_contours_area_of_image(thresh,contours,hirarchy,max_area=0.01,min_area=0.003)
        main_contours=self.filter_contours_area_of_image(thresh,contours,hirarchy,max_area=1,min_area=0.003)
        #interior_contours=self.filter_contours_area_of_image_interiors(thresh,contours,hirarchy,max_area=1,min_area=0)
    
        img_comm=np.zeros(thresh.shape)
        img_comm_in=cv2.fillPoly(img_comm, pts =main_contours, color=(255,255,255))
        ###img_comm_in=cv2.fillPoly(img_comm, pts =interior_contours, color=(0,0,0))
    
    
        img_comm_in=np.repeat(img_comm_in[:, :, np.newaxis], 3, axis=2)
        img_comm_in=img_comm_in.astype(np.uint8)
    
        imgray = cv2.cvtColor(img_comm_in, cv2.COLOR_BGR2GRAY)

    

        _, thresh = cv2.threshold(imgray, 0, 255, 0)

    
        contours,hirarchy=cv2.findContours(thresh.copy(), cv2.cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)


        contours_slope=contours#self.find_polugons_size_filter(contours,median_area=median_area,scaler_up=100,scaler_down=0.5)
    
        if len(contours_slope)>0:
            for jv in range(len(contours_slope)):
                new_poly=list(contours_slope[jv])
                if jv==0:
                    merged_all=new_poly
                else:
                    merged_all=merged_all+new_poly
    
    
            merge=np.array(merged_all)
    
    
            img_in=np.zeros(textline_mask.shape)
            img_p_in=cv2.fillPoly(img_in, pts =[merge], color=(255,255,255))
            
    
            
            rect = cv2.minAreaRect(merge)
            box = cv2.boxPoints(rect)
            box = np.int0(box)
    

    
            dst=self.rotate_image(textline_mask,slope)
            dst=dst[:,:,0]
            dst[dst!=0]=1
            
            
            contour_text_copy=contour_text_interest.copy()
            
            contour_text_copy[:,0,0]=contour_text_copy[:,0,0]-box_ind[0]#np.min(contour_text_interest_copy[:,0,0])
            contour_text_copy[:,0,1]=contour_text_copy[:,0,1]-box_ind[1]
            
            img_contour=np.zeros((box_ind[3],box_ind[2],3))
            img_contour=cv2.fillPoly(img_contour, pts =[contour_text_copy], color=(255,255,255))
            
            
            img_contour_rot=self.rotate_image(img_contour,slope)
            
            
            #img_comm_in=np.repeat(img_comm_in[:, :, np.newaxis], 3, axis=2)
            img_contour_rot=img_contour_rot.astype(np.uint8)
            imgrayrot = cv2.cvtColor(img_contour_rot, cv2.COLOR_BGR2GRAY)
            _, threshrot = cv2.threshold(imgrayrot, 0, 255, 0)
            contours_text_rot,_=cv2.findContours(threshrot.copy(), cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
            
            
            
            len_con_text_rot=[len(contours_text_rot[ib]) for ib in range(len(contours_text_rot))]
            ind_big_con=np.argmax(len_con_text_rot)
    
    
            textline_maskt=textline_mask[:,:,0]
            textline_maskt[textline_maskt!=0]=1
            
            
            sep_img,_,contours_rotated_clean=self.seperate_lines(dst,contours_text_rot[ind_big_con],slope)
    
            dst=self.rotate_image(sep_img,-slope)
    
            imgray = cv2.cvtColor(dst, cv2.COLOR_BGR2GRAY)
    
            _, thresh = cv2.threshold(imgray, 0, 255, 0)
    
    
    
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    
            found_polygons,_=cv2.findContours(thresh.copy(),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            
            
            img_in=np.zeros(textline_mask.shape)
            img_p_in=cv2.fillPoly(img_in, pts =found_polygons, color=(255,255,255))
        else:
    
            img_in=np.zeros(textline_mask.shape)
    
            img_p_in=cv2.fillPoly(img_in, pts =commenst_contours, color=(255,255,255))
            img_p_in = cv2.dilate(img_p_in, kernel, iterations=1)
            contours_rotated_clean=[]
        
        return img_p_in,contours_rotated_clean    
            
    def textline_contours_to_get_slope_correctly(self,textline_mask,img_patch,contour_interest):
        
        
        
        slope_new=0#deskew_images(img_patch)
        
        textline_mask=np.repeat(textline_mask[:, :, np.newaxis], 3, axis=2)*255
    
        textline_mask=textline_mask.astype(np.uint8)
        textline_mask = cv2.morphologyEx(textline_mask, cv2.MORPH_OPEN, self.kernel)
        textline_mask = cv2.morphologyEx(textline_mask, cv2.MORPH_CLOSE, self.kernel)
        textline_mask = cv2.erode(textline_mask,self.kernel,iterations = 1)
        imgray = cv2.cvtColor(textline_mask, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(imgray, 0, 255, 0)
    
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, self.kernel)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, self.kernel)
    
    
    
        contours,hirarchy=cv2.findContours(thresh.copy(), cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
        
    
    
        #commenst_contours=self.filter_contours_area_of_image(thresh,contours,hirarchy,max_area=0.01,min_area=0.003)
        main_contours=self.filter_contours_area_of_image(thresh,contours,hirarchy,max_area=1,min_area=0.003)
        #interior_contours=self.filter_contours_area_of_image_interiors(thresh,contours,hirarchy,max_area=1,min_area=0)
    
        
        textline_maskt=textline_mask[:,:,0]
        textline_maskt[textline_maskt!=0]=1
        
        _,peaks_point,_=self.seperate_lines(textline_maskt,contour_interest,slope_new)
    
        
        mean_dis=np.mean(np.diff(peaks_point))
        #mean_dis=np.median(np.diff(peaks_point))
        
        len_x=thresh.shape[1]
        #print(len_x,mean_dis,'x')
        
        slope_lines=[]
        contours_slope_new=[]
        for kk in range(len(main_contours)):
            
            xminh=np.min(main_contours[kk][:,0])
            xmaxh=np.max(main_contours[kk][:,0])
            
            yminh=np.min(main_contours[kk][:,1])
            ymaxh=np.max(main_contours[kk][:,1])
            
            
            #print(xminh,xmaxh ,yminh,ymaxh,ymaxh-yminh)
            
            if ymaxh-yminh<=mean_dis and (xmaxh-xminh)>=0.3*len_x:#xminh>=0.05*len_x and xminh<=0.4*len_x and xmaxh<=0.95*len_x and xmaxh>=0.6*len_x:
                contours_slope_new.append(main_contours[kk])
                
                rows,cols = thresh.shape[:2]
                [vx,vy,x,y] = cv2.fitLine(main_contours[kk], cv2.DIST_L2,0,0.01,0.01)
                
                slope_lines.append( (vy/vx)/np.pi*180 )

            if len(slope_lines)>=2:
    
                slope=np.mean(slope_lines)#slope_true/np.pi*180
            else:
                slope=999

        else:
            slope=0
        
        return slope
    def get_slopes_for_each_text_region(self,contours):
        
        #first let find the slop for biggest patch of text region
        
        index_max_area=np.argmax(self.area_of_cropped) 
        
        
        denoised=cv2.blur(self.all_text_images[index_max_area],(5,5))#otsu_copy(crop_img)#
        denoised=cv2.medianBlur(denoised,5)#cv2.GaussianBlur(crop_img, (5, 5), 0)
        denoised=cv2.GaussianBlur(denoised, (5, 5), 0)
        denoised=self.otsu_copy(denoised)
        denoised=denoised.astype(np.uint8)
        slope_biggest=self.textline_contours_to_get_slope_correctly(self.all_text_region_raw[index_max_area],denoised,contours[index_max_area])

        if np.abs(slope_biggest)>2.5:
            slope_biggest=0

    

        self.slopes=[]
        for mv in range(len(self.all_text_images)):
            denoised=cv2.blur(self.all_text_images[mv],(5,5))#otsu_copy(crop_img)#
            denoised=cv2.medianBlur(denoised,5)#cv2.GaussianBlur(crop_img, (5, 5), 0)
            denoised=cv2.GaussianBlur(denoised, (5, 5), 0)
            denoised=self.otsu_copy(denoised)
            denoised=denoised.astype(np.uint8)
            slope_for_all=self.textline_contours_to_get_slope_correctly(self.all_text_region_raw[mv],denoised,contours[mv])
            #text_patch_processed=textline_contours_postprocessing(gada)
        
            if np.abs(slope_for_all)>2.5 and slope_for_all!=999:
                slope_for_all=0
            elif slope_for_all==999:
                slope_for_all=slope_biggest
            self.slopes.append(slope_for_all)
            
    def deskew_textline_patches(self,contours,boxes):
        self.all_text_region_processed=[]
        self.all_found_texline_polygons=[]
        
        for jj in range(len(self.all_text_images)):
            #print(all_text_images[jj][0,0,0],np.unique(all_text_images[jj][:,:,0]))
            ###gada=self.all_text_images[jj][:,:,0]
            ###gada=(gada[:,:]==0)*1
            #print(gada[0,0])
            
            
            denoised=cv2.blur(self.all_text_images[jj],(5,5))#otsu_copy(crop_img)#
            denoised=cv2.medianBlur(denoised,5)#cv2.GaussianBlur(crop_img, (5, 5), 0)
            denoised=cv2.GaussianBlur(denoised, (5, 5), 0)
            denoised=self.otsu_copy(denoised)
            denoised=denoised.astype(np.uint8)
            text_patch_processed,cnt_clean_rot=self.textline_contours_postprocessing(self.all_text_region_raw[jj]
                                                                                     ,denoised,self.slopes[jj],contours[jj],boxes[jj])
            #text_patch_processed=textline_contours_postprocessing(gada)
            self.all_text_region_processed.append(text_patch_processed)
            
            text_patch_processed=text_patch_processed.astype(np.uint8)
            imgray = cv2.cvtColor(text_patch_processed, cv2.COLOR_BGR2GRAY)
        
            _, thresh = cv2.threshold(imgray, 0, 255, 0)
        
            self.found_polygons,_=cv2.findContours(thresh.copy(),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            ####all_found_texline_polygons.append(found_polygons)cnt_clean_rot
            self.all_found_texline_polygons.append(cnt_clean_rot)
            
            #img_v=np.zeros(text_patch_processed.shape)
            #img_v=cv2.fillPoly(img_v, pts =found_polygons, color=(255,255,255))
            #sumi=np.sum(np.sum(self.all_text_images[jj],axis=2),axis=1)
    

    def write_into_page_xml(self,contours,page_coord,dir_of_image):

        found_polygons_text_region=contours
        data = ET.Element('PcGts')
        
        data.set('xmlns',"http://schema.primaresearch.org/PAGE/gts/pagecontent/2017-07-15")
        data.set('xmlns:xsi',"http://www.w3.org/2001/XMLSchema-instance")
        data.set('xsi:schemaLocation',"http://schema.primaresearch.org/PAGE/gts/pagecontent/2017-07-15")
        #data.set('http',"http://schema.primaresearch.org/PAGE/gts/pagecontent/2018-07-15/pagecontent.xsd")
        
        
        
        metadata=ET.SubElement(data,'Metadata')
        
        author=ET.SubElement(metadata, 'Creator')
        author.text = 'Vahid'
        
        
        created=ET.SubElement(metadata, 'Created')
        created.text = '2019-06-17T18:15:12'
        
        changetime=ET.SubElement(metadata, 'LastChange')
        changetime.text = '2019-06-17T18:15:12' 
        
        
        
        page=ET.SubElement(data,'Page')
        
        page.set('imageFilename',self.f_name+'.tif')
        page.set('imageHeight',str(self.height_org)) 
        page.set('imageWidth',str(self.width_org))
        
        id_indexer=0
        
        for mm in range(len(found_polygons_text_region)):
            textregion=ET.SubElement(page, 'TextRegion')
        
            textregion.set('id','r'+str(id_indexer))
            id_indexer+=1
            
            if mm==0:
                textregion.set('type','heading')
            else:
                textregion.set('type','paragraph')
            coord_text = ET.SubElement(textregion, 'Coords')
            
            points_co=''
            for lmm in range(len(found_polygons_text_region[mm])):
                
                if len(found_polygons_text_region[mm][lmm])==2:
                    points_co=points_co+str( int( (found_polygons_text_region[mm][lmm][0] +page_coord[2])/self.scale_x ) )
                    points_co=points_co+','
                    points_co=points_co+str( int( (found_polygons_text_region[mm][lmm][1] +page_coord[0])/self.scale_y ) )
                else:
                    points_co=points_co+str( int((found_polygons_text_region[mm][lmm][0][0] +page_coord[2])/self.scale_x) )
                    points_co=points_co+','
                    points_co=points_co+str( int((found_polygons_text_region[mm][lmm][0][1] +page_coord[0])/self.scale_y) )
        
                if lmm<(len(found_polygons_text_region[mm])-1):
                    points_co=points_co+' '
            #print(points_co)
            coord_text.set('points',points_co)
            
            for j in range(len(self.all_found_texline_polygons[mm])):
        
                textline=ET.SubElement(textregion, 'TextLine')
                
                textline.set('id','l'+str(id_indexer))
                
                id_indexer+=1
                
        
                coord = ET.SubElement(textline, 'Coords')
        
                texteq=ET.SubElement(textline, 'TextEquiv')
        
                uni=ET.SubElement(texteq, 'Unicode')
                uni.text = ' ' 
        
                #points = ET.SubElement(coord, 'Points') 
        
                points_co=''
                for l in range(len(self.all_found_texline_polygons[mm][j])):
                    #point = ET.SubElement(coord, 'Point') 
        
        
        
                    #point.set('x',str(found_polygons[j][l][0]))  
                    #point.set('y',str(found_polygons[j][l][1]))
                    if len(self.all_found_texline_polygons[mm][j][l])==2:
                        points_co=points_co+str( int( (self.all_found_texline_polygons[mm][j][l][0] +page_coord[2]
                                                +self.all_box_coord[mm][2])/self.scale_x) )
                        points_co=points_co+','
                        points_co=points_co+str( int( (self.all_found_texline_polygons[mm][j][l][1] +page_coord[0]
                                                +self.all_box_coord[mm][0])/self.scale_y) )
                    else:
                        points_co=points_co+str( int( ( self.all_found_texline_polygons[mm][j][l][0][0] +page_coord[2]
                                                +self.all_box_coord[mm][2])/self.scale_x ) )
                        points_co=points_co+','
                        points_co=points_co+str( int( ( self.all_found_texline_polygons[mm][j][l][0][1] +page_coord[0]
                                                +self.all_box_coord[mm][0])/self.scale_y) ) 
        
                    if l<(len(self.all_found_texline_polygons[mm][j])-1):
                        points_co=points_co+' '
                #print(points_co)
                coord.set('points',points_co)
                
            texteqreg=ET.SubElement(textregion, 'TextEquiv')
        
            unireg=ET.SubElement(texteqreg, 'Unicode')
            unireg.text = ' ' 
        
            
            
        
        tree = ET.ElementTree(data)
        tree.write(dir_of_image+self.f_name+".xml")

    def run(self):
        self.get_image_and_scales()
        image_page,page_coord=self.extract_page()
        text_regions=self.extract_text_regions(image_page)
        boxes,contours=self.get_text_region_contours_and_boxes(text_regions)
        self.get_all_image_patches_based_on_text_regions(boxes,image_page)
        textline_mask_tot=self.textline_contours(image_page)
        self.get_textlines_for_each_textregions(textline_mask_tot,boxes)
        self.get_slopes_for_each_text_region(contours)
        self.deskew_textline_patches(contours,boxes)
        self.write_into_page_xml(contours,page_coord,self.dir_out)
def main():
    parser=argparse.ArgumentParser()
    
    parser.add_argument('-i','--image', dest='inp1', default=None, help='directory of image.')
    parser.add_argument('-o','--out', dest='inp2', default=None, help='directory to write output xml data.')
    parser.add_argument('-m','--model', dest='inp3', default=None, help='directory of models.')
    
    options=parser.parse_args()
    
    possibles=globals()
    possibles.update(locals())
    x=textlineerkenner(options.inp1,options.inp2,options.inp3)
    x.run()

if __name__=="__main__":
    main()

    
    
    

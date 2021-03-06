import time
import os

import tensorflow as tf
import tensorflow.contrib.eager as tfe
import numpy as np

from tqdm import tqdm, tqdm_notebook
from colorama import Fore, Style


class CNN_classification_single(tf.keras.Model):
    """ Classifier for Movie Review dataset(single channel)
    Args:
        num_words: uniwue words in dataset. including the words that is not in pretrained w2v model.
        in_dim: dimension of input array, which is maximum word counts among texts.
        out_dim: softmax output dimension.
        w2v_dim: word2vectors representationo dimension. 300 for GoogleNews-vectors-negative300 weights.
        is_static: if True, use static(no change in w2v pretrained weights), otherwise modify weights.
        learning_rate: for optimizer
        checkpoint_directory: checkpoint saving directory
        device_name: main device used for learning
    """

    def __init__(self,
                 num_words,
                 in_dim,
                 out_dim,
                 w2v_dim=300,
                 is_static=True,
                 learning_rate=1e-3,
                 checkpoint_directory="checkpoints/",
                 device_name="cpu:0"):
        super(CNN_classification_single, self).__init__()
        self.in_dim = in_dim
        self.num_words = num_words
        self.out_dim = out_dim

        self.w2v_dim = w2v_dim
        self.is_static = is_static
        self.learning_rate = learning_rate
        self.checkpoint_directory = checkpoint_directory
        if not os.path.exists(self.checkpoint_directory):
            os.makedirs(self.checkpoint_directory)
        self.device_name = device_name

        self.w2v = tf.keras.layers.Embedding(self.num_words, self.w2v_dim)

        if self.is_static:
            self.w2v.trainable=False

        self.conv11 = tf.layers.Conv1D(filters=100, kernel_size=3, padding="valid", name="conv11")
        self.conv12 = tf.layers.Conv1D(100, 4, padding="valid", name="conv12")
        self.conv13 = tf.layers.Conv1D(100, 5, padding="valid", name="conv13")

        self.flatten = tf.layers.Flatten(name="flatten")
        self.dropout = tf.layers.Dropout(0.5, name="dropout")

        self.out = tf.layers.Dense(self.out_dim, name="out")

        # optimizer
        self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate)

        # global step
        self.global_step = 0

    def copy_pretrained(self, weights):
        # initinate
        self.call(tf.convert_to_tensor(np.zeros([1, self.in_dim]), dtype="int64"), True)
        pretraind_array = tf.cast(tf.convert_to_tensor(weights), self.w2v.weights[0].dtype)
        tf.assign(self.w2v.weights[0], pretraind_array)

    def predict(self, X, training):
        """predicting output of the network
        Args:
            X : input tensor
            training : whether apply dropout or not
        """
        X = self.w2v(X)

        x11 = self.conv11(X)
        x12 = self.conv12(X)
        x13 = self.conv13(X)

        # Max-over-time pooling
        x11m = tf.reduce_max(x11, 1)
        x12m = tf.reduce_max(x12, 1)
        x13m = tf.reduce_max(x13, 1)

        xf1 = self.flatten(x11m)
        xf2 = self.flatten(x12m)
        xf3 = self.flatten(x13m)

        xf = tf.concat([xf1, xf2, xf3], axis=1)

        if training:
            xf = self.dropout(xf)

        pred = self.out(xf)
        return pred

    def call(self, X, training):
        return self.predict(X, training)

    def loss(self, X, y, training):
        """calculate loss of the batch
        Args:
            X : input tensor
            y : target label
            training : whether apply dropout or not
        """
        prediction = self.predict(X, training)
        loss_val = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=y, logits=prediction)
        loss_val = tf.reduce_sum(loss_val)
        loss_val += tf.nn.l2_loss(self.out.weights[0])

        return loss_val, prediction

    def grad(self, X, y, training):
        with tfe.GradientTape() as tape:
            loss_val, _ = self.loss(X, y, training)
        return tape.gradient(loss_val, self.variables), loss_val

    def fit(self, X_train, y_train, X_val, y_val, epochs=1, verbose=1, batch_size=32, saving=False, tqdm_option=None):
        """train the network
        Args:
            X_train : train dataset input
            y_train : train dataset label
            X_val : validation dataset input
            y_val = validation dataset input
            epochs : training epochs
            verbose : for which step it will print the loss and accuracy (and saving)
            batch_size : training batch size
            saving: whether to save checkpoint or not
            tqdm_option: tqdm logger option. default is none. use "normal" for tqdm, "notebook" for tqdm_notebook
        """

        def tqdm_wrapper(*args, **kwargs):
            if tqdm_option == "normal":
                return tqdm(*args, **kwargs)
            elif tqdm_option == "notebook":
                return tqdm_notebook(*args, **kwargs)
            else:
                return args[0]

        dataset_train = tf.data.Dataset.from_tensor_slices((X_train, y_train)).shuffle(999999999).batch(batch_size)
        batchlen_train = (len(X_train) - 1) // batch_size + 1

        dataset_val = tf.data.Dataset.from_tensor_slices((X_val, y_val)).shuffle(999999999).batch(batch_size)
        batchlen_val = (len(X_val) - 1) // batch_size + 1

        with tf.device(self.device_name):
            for i in range(epochs):
                epoch_loss = 0.0
                self.global_step += 1
                for X, y in tqdm_wrapper(dataset_train, total=batchlen_train, desc="TRAIN %3s" % self.global_step):
                    grads, batch_loss = self.grad(X, y, True)
                    mean_loss = tf.reduce_mean(batch_loss)
                    epoch_loss += mean_loss
                    self.optimizer.apply_gradients(zip(grads, self.variables))

                epoch_loss_val = 0.0
                val_accuracy = tf.contrib.eager.metrics.Accuracy()
                for X, y in tqdm_wrapper(dataset_val, total=batchlen_val, desc="VAL   %3s" % self.global_step):
                    batch_loss, pred = self.loss(X, y, False)
                    epoch_loss_val += tf.reduce_mean(batch_loss)
                    pred = tf.argmax(pred, axis=1)
                    pred = tf.cast(pred, y.dtype)
                    val_accuracy(pred, y)

                if i == 0 or ((i + 1) % verbose == 0):
                    print(Fore.RED + "=" * 25)
                    print("[EPOCH %d / STEP %d]" % ((i + 1), self.global_step))
                    print("TRAIN loss   : %.4f" % (epoch_loss / batchlen_train))
                    print("VAL   loss   : %.4f" % (epoch_loss_val / batchlen_val))
                    print("VAL   acc    : %.4f%%" % (val_accuracy.result().numpy() * 100))

                    if saving:
                        self.save()
                    print("=" * 25 + Style.RESET_ALL)
                time.sleep(1)

    def save(self):
        tfe.Saver(self.variables).save(self.checkpoint_directory, global_step=self.global_step)
        print("saved step %d in %s" % (self.global_step, self.checkpoint_directory))

    def load(self, global_step="latest"):
        # init
        self.call(tf.convert_to_tensor(np.zeros([1, self.in_dim]), dtype="int64"), True)

        saver = tfe.Saver(self.variables)
        if global_step == "latest":
            saver.restore(tf.train.latest_checkpoint(self.checkpoint_directory))
            self.global_step = int(tf.train.latest_checkpoint(self.checkpoint_directory).split('/')[-1][1:])
        else:
            saver.restore(self.checkpoint_directory + "-" + str(global_step))
            self.global_step = global_step


class CNN_classification_multi(tf.keras.Model):
    """ Classifier for Movie Review dataset(multi channel)
    Args:
        num_words: uniwue words in dataset. including the words that is not in pretrained w2v model.
        in_dim: dimension of input array, which is maximum word counts among texts.
        out_dim: softmax output dimension.
        w2v_dim: word2vectors representationo dimension. 300 for GoogleNews-vectors-negative300 weights.
        learning_rate: for optimizer
        checkpoint_directory: checkpoint saving directory
        device_name: main device used for learning
    """

    def __init__(self,
                 num_words,
                 in_dim,
                 out_dim,
                 w2v_dim=300,
                 learning_rate=1e-3,
                 checkpoint_directory="checkpoints/",
                 device_name="cpu:0"):
        super(CNN_classification_multi, self).__init__()
        self.in_dim = in_dim
        self.num_words = num_words
        self.out_dim = out_dim
        self.w2v_dim = w2v_dim
        self.learning_rate = learning_rate
        self.checkpoint_directory = checkpoint_directory
        if not os.path.exists(self.checkpoint_directory):
            os.makedirs(self.checkpoint_directory)
        self.device_name = device_name

        self.w2v_trainable = tf.keras.layers.Embedding(self.num_words, self.w2v_dim, name="w2v_trainable")

        self.w2v_nontrainable = tf.keras.layers.Embedding(self.num_words, self.w2v_dim, name="w2v_nontrainable")
        self.w2v_nontrainable.trainable = False


        self.conv11 = tf.layers.Conv1D(filters=100, kernel_size=3, padding="valid", name="conv11")
        self.conv12 = tf.layers.Conv1D(100, 4, padding="valid", name="conv12")
        self.conv13 = tf.layers.Conv1D(100, 5, padding="valid", name="conv13")

        self.flatten = tf.layers.Flatten(name="flatten")
        self.dropout = tf.layers.Dropout(0.5, name="dropout")

        self.out = tf.layers.Dense(self.out_dim, name="out")

        # optimizer
        self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate)

        # global step
        self.global_step = 0

    def copy_pretrained(self, weights):
        # initinate
        self.call(tf.convert_to_tensor(np.zeros([1, self.in_dim]), dtype="int64"), True)
        pretraind_array = tf.cast(tf.convert_to_tensor(weights), self.w2v_trainable.weights[0].dtype)
        tf.assign(self.w2v_trainable.weights[0], pretraind_array)
        tf.assign(self.w2v_nontrainable.weights[0], pretraind_array)


    def predict(self, X, training):
        """predicting output of the network
        Args:
            X : input tensor
            training : whether apply dropout or not
        """
        X_notrain = self.w2v_nontrainable(X)
        X_train = self.w2v_trainable(X)

        x11_notrain = self.conv11(X_notrain)
        x12_notrain = self.conv12(X_notrain)
        x13_notrain = self.conv13(X_notrain)

        # Max-over-time pooling
        x11m_notrain = tf.reduce_max(x11_notrain, 1)
        x12m_notrain = tf.reduce_max(x12_notrain, 1)
        x13m_notrain = tf.reduce_max(x13_notrain, 1)

        xf1_notrain = self.flatten(x11m_notrain)
        xf2_notrain = self.flatten(x12m_notrain)
        xf3_notrain = self.flatten(x13m_notrain)

        xf_notrain = tf.concat([xf1_notrain, xf2_notrain, xf3_notrain], axis=1)

        x11_train = self.conv11(X_train)
        x12_train = self.conv12(X_train)
        x13_train = self.conv13(X_train)

        # Max-over-time pooling
        x11m_train = tf.reduce_max(x11_train, 1)
        x12m_train = tf.reduce_max(x12_train, 1)
        x13m_train = tf.reduce_max(x13_train, 1)

        xf1_train = self.flatten(x11m_train)
        xf2_train = self.flatten(x12m_train)
        xf3_train = self.flatten(x13m_train)

        xf_train = tf.concat([xf1_train, xf2_train, xf3_train], axis=1)

        xf = xf_notrain + xf_train

        if training:
            xf = self.dropout(xf)

        pred = self.out(xf)
        return pred

    def call(self, X, training):
        return self.predict(X, training)

    def loss(self, X, y, training):
        """calculate loss of the batch
        Args:
            X : input tensor
            y : target label
            training : whether apply dropout or not
        """
        prediction = self.predict(X, training)
        loss_val = tf.nn.sparse_softmax_cross_entropy_with_logits(labels=y, logits=prediction)
        loss_val = tf.reduce_sum(loss_val)
        loss_val += tf.nn.l2_loss(self.out.weights[0])

        return loss_val, prediction

    def grad(self, X, y, training):
        with tfe.GradientTape() as tape:
            loss_val, _ = self.loss(X, y, training)
        return tape.gradient(loss_val, self.variables), loss_val

    def fit(self, X_train, y_train, X_val, y_val, epochs=1, verbose=1, batch_size=32, saving=False, tqdm_option=None):
        """train the network
        Args:
            X_train : train dataset input
            y_train : train dataset label
            X_val : validation dataset input
            y_val = validation dataset input
            epochs : training epochs
            verbose : for which step it will print the loss and accuracy (and saving)
            batch_size : training batch size
            saving: whether to save checkpoint or not
            tqdm_option: tqdm logger option. default is none. use "normal" for tqdm, "notebook" for tqdm_notebook
        """

        def tqdm_wrapper(*args, **kwargs):
            if tqdm_option == "normal":
                return tqdm(*args, **kwargs)
            elif tqdm_option == "notebook":
                return tqdm_notebook(*args, **kwargs)
            else:
                return args[0]

        dataset_train = tf.data.Dataset.from_tensor_slices((X_train, y_train)).shuffle(999999999).batch(batch_size)
        batchlen_train = (len(X_train) - 1) // batch_size + 1

        dataset_val = tf.data.Dataset.from_tensor_slices((X_val, y_val)).shuffle(999999999).batch(batch_size)
        batchlen_val = (len(X_val) - 1) // batch_size + 1

        with tf.device(self.device_name):
            for i in range(epochs):
                epoch_loss = 0.0
                self.global_step += 1
                for X, y in tqdm_wrapper(dataset_train, total=batchlen_train, desc="TRAIN %3s" % self.global_step):
                    grads, batch_loss = self.grad(X, y, True)
                    mean_loss = tf.reduce_mean(batch_loss)
                    epoch_loss += mean_loss
                    self.optimizer.apply_gradients(zip(grads, self.variables))

                epoch_loss_val = 0.0
                val_accuracy = tf.contrib.eager.metrics.Accuracy()
                for X, y in tqdm_wrapper(dataset_val, total=batchlen_val, desc="VAL   %3s" % self.global_step):
                    batch_loss, pred = self.loss(X, y, False)
                    epoch_loss_val += tf.reduce_mean(batch_loss)
                    pred = tf.argmax(pred, axis=1)
                    pred = tf.cast(pred, y.dtype)
                    val_accuracy(pred, y)

                if i == 0 or ((i + 1) % verbose == 0):
                    print(Fore.RED + "=" * 25)
                    print("[EPOCH %d / STEP %d]" % ((i + 1), self.global_step))
                    print("TRAIN loss   : %.4f" % (epoch_loss / batchlen_train))
                    print("VAL   loss   : %.4f" % (epoch_loss_val / batchlen_val))
                    print("VAL   acc    : %.4f%%" % (val_accuracy.result().numpy() * 100))

                    if saving:
                        self.save()
                    print("=" * 25 + Style.RESET_ALL)
                time.sleep(1)

    def save(self):
        tfe.Saver(self.variables).save(self.checkpoint_directory, global_step=self.global_step)
        print("saved step %d in %s" % (self.global_step, self.checkpoint_directory))

    def load(self, global_step="latest"):
        # init
        self.call(tf.convert_to_tensor(np.zeros([1, self.in_dim]), dtype="int64"), True)

        saver = tfe.Saver(self.variables)
        if global_step == "latest":
            saver.restore(tf.train.latest_checkpoint(self.checkpoint_directory))
            self.global_step = int(tf.train.latest_checkpoint(self.checkpoint_directory).split('/')[-1][1:])
        else:
            saver.restore(self.checkpoint_directory + "-" + str(global_step))
            self.global_step = global_step


class CNN_regression_single(tf.keras.Model):
    """ regressor for amazon review dataset(single channel)
    Args:
        num_words: uniwue words in dataset. including the words that is not in pretrained w2v model.
        in_dim: dimension of input array, which is maximum word counts among texts.
        out_dim: regression output dimension(1).
        w2v_dim: word2vectors representationo dimension. 300 for GoogleNews-vectors-negative300 weights.
        is_static: if True, use static(no change in w2v pretrained weights), otherwise modify weights.
        learning_rate: for optimizer
        checkpoint_directory: checkpoint saving directory
        device_name: main device used for learning
    """

    def __init__(self,
                 num_words,
                 in_dim,
                 out_dim=1,
                 w2v_dim=300,
                 is_static=True,
                 learning_rate=1e-3,
                 checkpoint_directory="checkpoints/",
                 device_name="cpu:0"):
        super(CNN_regression_single, self).__init__()
        self.in_dim = in_dim
        self.num_words = num_words
        self.out_dim = out_dim

        self.w2v_dim = w2v_dim
        self.is_static = is_static
        self.learning_rate = learning_rate
        self.checkpoint_directory = checkpoint_directory
        if not os.path.exists(self.checkpoint_directory):
            os.makedirs(self.checkpoint_directory)
        self.device_name = device_name

        self.w2v = tf.keras.layers.Embedding(self.num_words, self.w2v_dim)

        if self.is_static:
            self.w2v.trainable=False

        self.conv11 = tf.layers.Conv1D(filters=100, kernel_size=3, padding="valid", name="conv11")
        self.conv12 = tf.layers.Conv1D(100, 4, padding="valid", name="conv12")
        self.conv13 = tf.layers.Conv1D(100, 5, padding="valid", name="conv13")

        self.flatten = tf.layers.Flatten(name="flatten")
        self.dropout = tf.layers.Dropout(0.5, name="dropout")

        self.out = tf.layers.Dense(self.out_dim, name="out", activation=tf.nn.sigmoid)

        # optimizer
        self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate)

        # global step
        self.global_step = 0


    def copy_pretrained(self, weights):
        # initinate
        self.call(tf.convert_to_tensor(np.zeros([1, self.in_dim]), dtype="int64"), True)
        pretraind_array = tf.cast(tf.convert_to_tensor(weights), self.w2v.weights[0].dtype)
        tf.assign(self.w2v.weights[0], pretraind_array)

    def predict(self, X, training):
        """predicting output of the network
        Args:
            X : input tensor
            training : whether apply dropout or not
        """
        X = self.w2v(X)

        x11 = self.conv11(X)
        x12 = self.conv12(X)
        x13 = self.conv13(X)

        # Max-over-time pooling
        x11m = tf.reduce_max(x11, 1)
        x12m = tf.reduce_max(x12, 1)
        x13m = tf.reduce_max(x13, 1)

        xf1 = self.flatten(x11m)
        xf2 = self.flatten(x12m)
        xf3 = self.flatten(x13m)

        xf = tf.concat([xf1, xf2, xf3], axis=1)

        if training:
            xf = self.dropout(xf)

        pred = self.out(xf)
        return pred

    def call(self, X, training):
        return self.predict(X, training)

    def loss(self, X, y, training):
        """calculate loss of the batch
        Args:
            X : input tensor
            y : target label
            training : whether apply dropout or not
        """
        prediction = self.predict(X, training)
        loss_val = tf.losses.mean_squared_error(y, prediction, reduction=tf.losses.Reduction.NONE)
        loss_val = tf.reduce_sum(loss_val)
        loss_val += tf.nn.l2_loss(self.out.weights[0])

        return loss_val, prediction

    def grad(self, X, y, training):
        with tfe.GradientTape() as tape:
            loss_val, _ = self.loss(X, y, training)
        return tape.gradient(loss_val, self.variables), loss_val

    def fit(self, X_train, y_train, X_val, y_val, epochs=1, verbose=1, batch_size=32, saving=False, tqdm_option=None):
        """train the network
        Args:
            X_train : train dataset input
            y_train : train dataset label
            X_val : validation dataset input
            y_val = validation dataset input
            epochs : training epochs
            verbose : for which step it will print the loss and accuracy (and saving)
            batch_size : training batch size
            saving: whether to save checkpoint or not
            tqdm_option: tqdm logger option. default is none. use "normal" for tqdm, "notebook" for tqdm_notebook
        """

        def tqdm_wrapper(*args, **kwargs):
            if tqdm_option == "normal":
                return tqdm(*args, **kwargs)
            elif tqdm_option == "notebook":
                return tqdm_notebook(*args, **kwargs)
            else:
                return args[0]

        dataset_train = tf.data.Dataset.from_tensor_slices((X_train, y_train)).shuffle(999999999).batch(batch_size)
        batchlen_train = (len(X_train) - 1) // batch_size + 1

        dataset_val = tf.data.Dataset.from_tensor_slices((X_val, y_val)).shuffle(999999999).batch(batch_size)
        batchlen_val = (len(X_val) - 1) // batch_size + 1

        with tf.device(self.device_name):
            for i in range(epochs):
                epoch_loss = 0.0
                self.global_step += 1
                for X, y in tqdm_wrapper(dataset_train, total=batchlen_train, desc="TRAIN %3s" % self.global_step):
                    grads, batch_loss = self.grad(X, y, True)
                    mean_loss = tf.reduce_mean(batch_loss)
                    epoch_loss += mean_loss
                    self.optimizer.apply_gradients(zip(grads, self.variables))

                epoch_loss_val = 0.0
                for X, y in tqdm_wrapper(dataset_val, total=batchlen_val, desc="VAL   %3s" % self.global_step):
                    batch_loss, pred = self.loss(X, y, False)
                    epoch_loss_val += tf.reduce_mean(batch_loss)

                if i == 0 or ((i + 1) % verbose == 0):
                    print(Fore.RED + "=" * 25)
                    print("[EPOCH %d / STEP %d]" % ((i + 1), self.global_step))
                    print("TRAIN loss   : %.4f" % (epoch_loss / batchlen_train))
                    print("VAL   loss   : %.4f" % (epoch_loss_val / batchlen_val))

                    if saving:
                        self.save()
                    print("=" * 25 + Style.RESET_ALL)
                time.sleep(1)

    def save(self):
        tfe.Saver(self.variables).save(self.checkpoint_directory, global_step=self.global_step)
        print("saved step %d in %s" % (self.global_step, self.checkpoint_directory))

    def load(self, global_step="latest"):
        # init
        self.call(tf.convert_to_tensor(np.zeros([1, self.in_dim]), dtype="int64"), True)

        saver = tfe.Saver(self.variables)
        if global_step == "latest":
            saver.restore(tf.train.latest_checkpoint(self.checkpoint_directory))
            self.global_step = int(tf.train.latest_checkpoint(self.checkpoint_directory).split('/')[-1][1:])
        else:
            saver.restore(self.checkpoint_directory + "-" + str(global_step))
            self.global_step = global_step

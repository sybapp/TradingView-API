# Require walk-forward validation

The strategy evolution loop will require walk-forward validation from the first version. Because candidate strategies and parameters are searched repeatedly, final scoring must use future windows that were not part of the search window to reduce in-sample overfitting.

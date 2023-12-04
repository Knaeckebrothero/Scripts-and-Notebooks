% Import data
data = readtable('data\irisPlusRandom.csv');
numColumns = width(data);

% Iterate through all columns except the last one
for i = 1:numColumns-1
    currentColumn = data.(i);
    if iscell(currentColumn)
        % Convert cell array of strings to char array for processing
        charArray = char(currentColumn);
        % Replace ',' with '.' and then convert to double
        currentColumn = str2double(strrep(charArray, ',', '.'));
        % Assign the converted column back to the table
        data.(i) = currentColumn;
    elseif isnumeric(currentColumn)
        % If the column is already numeric, ensure it's of type double
        data.(i) = double(currentColumn);
    end
end

%{

% Remove rows where all elements are NaN or empty
data = data(~all(ismissing(data), 2), :);

% Convert columns to double
numColumns = width(data);
for i = 1:numColumns-1
    if ~isa(data{:,i}, 'double')
        data{:,i} = str2double(strrep(data{:,i}, ',', '.'));
    end
end

% Select features for prediction
features = data(:, {'sepal_width', 'petal_length', 'petal_width', 'random'});

% Create the regression model
mdl = fitlm(features, data.sepal_length);

% Make predictions (using the same dataset for simplicity)
predictions = predict(mdl, features);

%}

% Now, proceed with the regression
features = data(1:145, 2:5);
response = data.sepal_length(1:145);

% Create and fit the model
mdl = fitlm(features, response);
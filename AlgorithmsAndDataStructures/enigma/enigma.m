function encoded_text = enigma(rotors, plugboard_in, plugboard_out, plain_text)
%{
My take on a polyalphabetic substitution cipher

Params:
    rotors = list of values from 1 - 9 to simulate the encyption cylinders
    plugboard_in = list of characters that should be replaced
    plugboard_out = list of characters they should be replaced with
    plain_text = text to be encoded

Returns:
    The encrypted ciphertext

Description:
    It works simular to the original enigma machine machine used in ww2,
    with just a few minor tweaks and additions.
    
    The machine can have multiple cylinder wheels that turn after a charakter
    has been encryped, but while the original used a "simple" substitution
    method, mine is using a multiplicative cipher to encrypt the characters.
    
    Just like the original this implementation also can make use of a
    plugboard to map any character to any other character (mapping is beeing
    applied before and after encryption just like on the original).
%}

    % Define a function to turn the wheels of the machine
    function rotors = rotate()

        % Start with one to simulare multiple rotors with different turning speeds
        roation = 1;

        % Loop over the rotors to turn them
        for i = 1:length(rotors)
            % Turn the first rotor
            rotors(i) = mod(rotors(i) + roation, 9);

            % Then decrease rotation value to lower rotationspeed with ever iteration
            roation = roation / 2;
        end
    end

    % Define a function to map characters to others via the plugbord
    function char = plug_charakters(input_char)

        % Return original character if not included in plugboard list
        char = input_char;

        % Iterate of the plugboard contents to map them
        for i = 1:length(plugboard_in)

            % Check if the character should be maped
            if plugboard_in(i) == input_char

                % Map charakter to output charakter and break loop
                char = plugboard_out(i);
                break
            end
        end
    end

    % Declare the output variable as an empty string and convert input
    encoded_text = '';
    plain_text = upper(plain_text);

    % Main encryption loop iterating over the text, to encrypt it
    for i = 1:length(plain_text)

        % Map charakter before encryption
        encoded_char = plug_charakters(plain_text(i));
    
        % Encrypt char using the encryption rotors
        for r = 1:length(rotors)

            % Apply multiplicative cipher for every wheel
            encoded_char = mod(encoded_char * rotors(r), 26);
        end
    
        % Map charakter after encryption and turn wheels
        encoded_text(i) = plug_charakters(encoded_char);
        rotate()
    end
end
